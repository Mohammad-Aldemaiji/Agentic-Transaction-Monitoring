"""The local language model: Ollama on this machine, nothing else.

The agent refuses to start if Ollama is reached over the network or the model tag
is an Ollama cloud model, so transaction data cannot leave the machine by accident.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

from src import config

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class LLMUnavailable(RuntimeError):
    """Ollama isn't running, the model isn't pulled, or the settings aren't local."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., str]

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def schema(required=(), **props) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    steps: int = 0
    seconds: float = 0.0          # time spent waiting for the model
    tool_calls: list = field(default_factory=list)


def check_local(host: str = config.OLLAMA_HOST, model: str = config.AGENT_MODEL) -> None:
    h = urlparse(host if "://" in host else "http://" + host).hostname
    if h not in LOCAL_HOSTS:
        raise LLMUnavailable(f"OLLAMA_HOST is {host}. Transaction data may only go to a model "
                             "on this machine, so point it at localhost.")
    if "cloud" in model.lower():
        raise LLMUnavailable(f"{model} is an Ollama cloud model, which runs on Ollama's servers. "
                             "Use a local tag such as qwen3:8b.")


_client = None


def client():
    """A checked Ollama client. Raises LLMUnavailable with a message an analyst can act on."""
    global _client
    if _client is not None:
        return _client
    import ollama
    check_local()
    c = ollama.Client(host=config.OLLAMA_HOST)
    try:
        info = c.show(config.AGENT_MODEL)
    except ollama.ResponseError:
        raise LLMUnavailable(f"Model {config.AGENT_MODEL} isn't downloaded. Run: ollama pull {config.AGENT_MODEL}")
    except Exception as e:
        raise LLMUnavailable(f"Can't reach Ollama at {config.OLLAMA_HOST} ({type(e).__name__}). "
                             "Start the Ollama app or run `ollama serve`.")
    if info.capabilities and "tools" not in info.capabilities:
        raise LLMUnavailable(f"{config.AGENT_MODEL} doesn't support tool calling. Use qwen3:8b "
                             "or another model tagged 'tools' on ollama.com/library.")
    _client = c
    return c


def status() -> dict:
    try:
        client()
        return {"ok": True, "model": config.AGENT_MODEL, "host": config.OLLAMA_HOST}
    except LLMUnavailable as e:
        return {"ok": False, "model": config.AGENT_MODEL, "host": config.OLLAMA_HOST, "error": str(e)}


KEEP_ALIVE = "60m"  # keep the model in memory between cases instead of reloading it


def options() -> dict:
    # The same options on every call: Ollama reloads the model if num_ctx changes.
    return {"temperature": 0, "seed": 42, "num_ctx": config.AGENT_NUM_CTX}


def warm_up() -> str:
    """Load the model now, so the first alert doesn't wait for it (or hit a cold-start crash)."""
    t = time.perf_counter()
    client().chat(model=config.AGENT_MODEL, messages=[{"role": "user", "content": "Reply with OK."}],
                  think=False, options={**options(), "num_predict": 1}, keep_alive=KEEP_ALIVE)
    return f"{config.AGENT_MODEL} loaded in {time.perf_counter() - t:.1f} s"


def parse_json(text: str):
    """The first {...} object in a reply, or None."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return json.loads(text[start:end])
    except ValueError:
        return None


def run(system: str, user: str, tools: list, *, must_call: str = None,
        on_tool: Callable[[str, dict, str], None] = None, max_steps: int = 20):
    """Tool-calling loop. Returns (parsed final JSON or None, raw final text, Usage).

    must_call: a tool the model has to call before it may finish (it gets one reminder).
    """
    c = client()
    by_name = {t.name: t for t in tools}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    u = Usage()
    nudged_call = nudged_json = False
    for _ in range(max_steps):
        t0 = time.perf_counter()
        r = c.chat(model=config.AGENT_MODEL, messages=msgs, tools=[t.spec() for t in tools],
                   think=config.AGENT_THINK, options=options(), keep_alive=KEEP_ALIVE)
        u.seconds += time.perf_counter() - t0
        u.steps += 1
        u.tokens_in += r.prompt_eval_count or 0
        u.tokens_out += r.eval_count or 0
        m = r.message
        msgs.append(m)
        if m.tool_calls:
            for call in m.tool_calls:
                name, args = call.function.name, dict(call.function.arguments or {})
                tool = by_name.get(name)
                try:
                    result = tool.fn(**args) if tool else f"Unknown tool {name}."
                except Exception as e:  # let the model see the error and retry
                    result = f"Tool error: {e}"
                u.tool_calls.append(name)
                if on_tool:
                    on_tool(name, args, result)
                msgs.append({"role": "tool", "tool_name": name, "content": str(result)})
            continue
        text = m.content or ""
        # Small models sometimes stop early: one reminder each, no more.
        if must_call and must_call not in u.tool_calls and not nudged_call:
            nudged_call = True
            msgs.append({"role": "user", "content": f"You have not called {must_call} yet. "
                         "Do that before giving the final JSON."})
            continue
        parsed = parse_json(text)
        if parsed is None and not nudged_json:
            nudged_json = True
            msgs.append({"role": "user", "content": "Reply with only the final JSON object."})
            continue
        return parsed, text, u
    return None, "Step limit reached without a decision.", u
