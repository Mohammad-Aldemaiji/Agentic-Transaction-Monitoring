"""
Fraud investigation agent, running on a local model.

Nothing leaves this machine: the language model runs in Ollama on localhost,
and the call demo's voice (Piper) and speech recognition (faster-whisper) run
locally too. No API keys, no cloud calls.

Setup (once):
    1. Install Ollama from https://ollama.com/download
    2. ollama pull qwen3:8b
    3. pip install -r src/agent/requirements.txt

Put transactions.csv in the repo's data/ folder (it is not in Git).

Usage:
    python src/agent/fraud_agent.py pick          # list test alerts (with ground truth, for YOU only)
    python src/agent/fraud_agent.py TX000123456   # investigate one alert in the terminal
    python src/agent/call_server.py               # the voice-call demo in the browser

Settings (environment variables, all optional):
    AGENT_MODEL      Ollama model tag             default qwen3:8b
    OLLAMA_HOST      where Ollama listens         default http://127.0.0.1:11434
    AGENT_NUM_CTX    context window in tokens     default 8192
    AGENT_THINK      1 = let the model reason before each step (slower)   default 0

In the terminal you play two roles:
    - the CUSTOMER, when the agent calls the customer
    - the ANALYST, when the agent asks to execute an action
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

PROVIDER = "ollama (local)"
MODEL = os.environ.get("AGENT_MODEL", "qwen3:8b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
NUM_CTX = int(os.environ.get("AGENT_NUM_CTX", "8192"))
THINK = os.environ.get("AGENT_THINK", "0") == "1"
SEED = 42          # fixed seed and temperature 0 so a run can be repeated (NFR4)
HERE = Path(__file__).resolve().parent   # always look next to this script
ROOT = HERE.parents[1]                   # repo root (this file is in src/agent/)
DATA = ROOT / "data" / "transactions.csv"
CACHE = ROOT / "data" / "transactions_agent.parquet"

# What the agent is allowed to see. Labels are loaded but never passed to it.
# residency_status / corridor fields are left out on purpose (fairness note in the README).
COLS = [
    "txn_id", "timestamp", "account_id", "direction", "amount_sar", "channel",
    "entry_mode", "merchant_category", "merchant_name", "merchant_country",
    "counterparty_id", "counterparty_type", "counterparty_country", "status",
    "decline_reason", "device_id", "ip_country", "is_proxy_vpn", "auth_method",
    "is_3ds_authenticated", "seconds_since_login", "txn_city",
    "distance_from_home_km", "customer_type", "home_city", "account_age_days",
    "declared_monthly_income", "amount_over_p95_est", "is_new_counterparty",
    "beneficiary_age_days", "is_new_device", "is_new_country", "txn_count_1h",
    "declined_count_24h", "accounts_sharing_device", "impossible_travel_kmh",
    "cnp_ratio_24h", "small_then_large_flag", "balance_before",
]
LABELS = ["is_fraud", "fraud_type"]

ACTION_LOG = []  # the audit trail
ALERT_TIME = None  # set per investigation; tools never see data after it
CALL_STATE = "none"  # "none" -> "active" -> "ended": one call per investigation
ACTIONS = ["BLOCK_CARD", "HOLD_TRANSFER", "FREEZE_DIGITAL_ACCESS",
           "FREEZE_ACCOUNT", "STEP_UP_AUTH", "NO_ACTION"]


# ------------------------------------------------ hooks (terminal by default)
# call_server.py replaces these with browser versions; the agent logic is shared.

def terminal_ask_customer(question: str) -> str:
    print(f"\n[CALL TO REGISTERED NUMBER] agent: {question}")
    return input("  customer> ")


def terminal_end_call() -> None:
    print("[CALL ENDED] agent: Thank you. The bank will follow up with you shortly.")


def terminal_ask_approval(action: str, reason: str) -> bool:
    print(f"\n[APPROVAL REQUEST] {action}\n  reason: {reason}")
    return input("  analyst approve? (y/n)> ").strip().lower() == "y"


def terminal_emit(event: dict) -> None:
    if event["type"] == "tool":
        print(f"  -> {event['name']}({json.dumps(event['args'], ensure_ascii=False)})")
    elif event["type"] == "tokens":
        print(f"\n[tokens] in={event['in']:,} out={event['out']:,}")
    elif event["type"] == "notice":
        print(f"  ({event['text']})")


ASK_CUSTOMER = terminal_ask_customer
ASK_APPROVAL = terminal_ask_approval
END_CALL = terminal_end_call
EMIT = terminal_emit


def load():
    if not DATA.exists() and not CACHE.exists():
        sys.exit(f"Can't find transactions.csv. Put it in this folder:\n  {HERE}")
    if CACHE.exists():
        df = pd.read_parquet(CACHE)
    else:
        print("First run: reading the CSV (takes a minute), caching as parquet...")
        df = pd.read_csv(DATA, usecols=COLS + LABELS)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.to_parquet(CACHE)
    return df.set_index("txn_id", drop=False)


DF = load()


def _ts(value: str) -> pd.Timestamp:
    """Parse a timestamp and cap it at the alert time (no looking into the future)."""
    t = pd.Timestamp(value)
    t = t.tz_localize("Asia/Riyadh") if t.tzinfo is None else t
    return min(t, ALERT_TIME) if ALERT_TIME is not None else t


# ---------------------------------------------------------------- tools

def get_transaction(txn_id: str) -> str:
    """Get the full details of a flagged transaction: amount, channel, merchant,
    device, session, location and behavioural features.

    Args:
        txn_id: The transaction id, for example TX000000123
    """
    if txn_id not in DF.index:
        return f"Transaction {txn_id} not found."
    row = DF.loc[txn_id].drop(LABELS)
    row["timestamp"] = str(row["timestamp"])  # keep Riyadh local time, not UTC
    return row.to_json()


def get_account_history(account_id: str, before_timestamp: str) -> str:
    """Summarise the account's normal behaviour over the 90 days before a given
    time: typical amounts, channels, cities, countries, hours, devices, and the
    last 10 transactions.

    Args:
        account_id: The account id (IBAN) from the transaction
        before_timestamp: Only history strictly before this ISO timestamp is used
    """
    t = _ts(before_timestamp)
    h = DF[(DF.account_id == account_id) & (DF.timestamp < t)
           & (DF.timestamp >= t - pd.Timedelta(days=90))]
    if h.empty:
        return "No prior history in the last 90 days."
    return json.dumps({
        "n_txns_90d": len(h),
        "median_amount_sar": round(float(h.amount_sar.median()), 2),
        "p95_amount_sar": round(float(h.amount_sar.quantile(0.95)), 2),
        "top_channels": h.channel.value_counts().head(4).to_dict(),
        "top_cities": h.txn_city.value_counts().head(4).to_dict(),
        "merchant_countries_seen": sorted(h.merchant_country.dropna().unique().tolist())[:10],
        "ip_countries_seen": sorted(h.ip_country.dropna().unique().tolist())[:10],
        "distinct_devices": int(h.device_id.nunique()),
        "usual_hours": h.timestamp.dt.hour.value_counts().head(4).index.tolist(),
        "last_10": h.tail(10)[["timestamp", "direction", "amount_sar", "channel",
                               "merchant_name", "txn_city", "status"]].to_dict("records"),
    }, default=str)


def get_device_links(device_id: str, before_timestamp: str) -> str:
    """List the accounts that used this device up to a given time, with when each
    first appeared on it. Many accounts on one device can indicate takeover or
    mules, but households also share devices.

    Args:
        device_id: The device id from the transaction
        before_timestamp: Only activity up to this ISO timestamp is used
    """
    d = DF[(DF.device_id == device_id) & (DF.timestamp <= _ts(before_timestamp))]
    if d.empty:
        return "Device not seen before."
    first = d.groupby("account_id").timestamp.min().sort_values()
    return json.dumps({"n_accounts": len(first),
                       "accounts_first_seen": first.astype(str).to_dict()})


def call_customer(question: str) -> str:
    """Speak to the customer on a phone call to their REGISTERED phone number
    (a separate channel from the banking app, which a fraudster may control)
    and return what they say back. The call opens with a fixed security notice,
    so do not greet or introduce yourself. Say one or two short spoken sentences
    per turn. Ask only whether they made specific transactions, naming amount,
    date and merchant or beneficiary name. Never reveal what the bank detected
    (IP, country, VPN, device, risk signals), never read out account numbers,
    and never ask for a PIN, OTP, password or card number. Call again to ask a
    follow-up question if the customer may have been manipulated into making a
    payment themselves.

    Args:
        question: What to say to the customer on this turn of the call
    """
    global CALL_STATE
    if CALL_STATE == "ended":
        EMIT({"type": "notice", "text": "Blocked: the agent tried to call again after the call ended"})
        return "The call has already ended and cannot be restarted. Continue with what you know."
    CALL_STATE = "active"
    reply = ASK_CUSTOMER(question)
    if CALL_STATE == "ended":  # the customer hung up or never answered
        return reply
    return "Customer said: " + reply


def end_call() -> str:
    """Hang up the phone call once you have asked all your questions. The customer
    hears a fixed closing line saying the bank will follow up. You must end the
    call before proposing any action.
    """
    global CALL_STATE
    if CALL_STATE != "active":
        return "There is no call in progress."
    END_CALL()
    CALL_STATE = "ended"
    return "Call ended. The customer was told the bank will follow up."


def propose_action(action: str, reason: str) -> str:
    """Propose a protective action. It only executes if the human analyst approves.
    Choose the least severe action that contains the risk.

    Args:
        action: One of BLOCK_CARD, HOLD_TRANSFER, FREEZE_DIGITAL_ACCESS,
                FREEZE_ACCOUNT, STEP_UP_AUTH, NO_ACTION
        reason: One or two sentences the analyst will read before approving
    """
    if action not in ACTIONS:
        return f"Invalid action. Choose one of {ACTIONS}."
    if CALL_STATE == "active":
        EMIT({"type": "notice", "text": "Blocked: the agent tried to propose an action before ending the call"})
        return "Not allowed yet: end the call with end_call before proposing an action."
    ok = ASK_APPROVAL(action, reason)
    ACTION_LOG.append({"action": action, "reason": reason,
                       "approved": ok, "executed": ok})
    return f"{action} EXECUTED (mock core-banking API)." if ok else f"{action} REJECTED by analyst."


TOOLS = {f.__name__: f for f in [get_transaction, get_account_history,
                                 get_device_links, call_customer, end_call, propose_action]}


def _schema(props, required):
    return {"type": "object", "properties": props, "required": required}


TOOL_SPECS = [
    {"name": "get_transaction", "description": get_transaction.__doc__,
     "input_schema": _schema({"txn_id": {"type": "string"}}, ["txn_id"])},
    {"name": "get_account_history", "description": get_account_history.__doc__,
     "input_schema": _schema({"account_id": {"type": "string"},
                              "before_timestamp": {"type": "string"}},
                             ["account_id", "before_timestamp"])},
    {"name": "get_device_links", "description": get_device_links.__doc__,
     "input_schema": _schema({"device_id": {"type": "string"},
                              "before_timestamp": {"type": "string"}},
                             ["device_id", "before_timestamp"])},
    {"name": "call_customer", "description": call_customer.__doc__,
     "input_schema": _schema({"question": {"type": "string"}}, ["question"])},
    {"name": "end_call", "description": end_call.__doc__,
     "input_schema": _schema({}, [])},
    {"name": "propose_action", "description": propose_action.__doc__,
     "input_schema": _schema({"action": {"type": "string", "enum": ACTIONS},
                              "reason": {"type": "string"}},
                             ["action", "reason"])},
]

SYSTEM_TEMPLATE = """You are a fraud investigation agent at a Saudi retail bank. An alert has
been raised on a transaction. Investigate it with your tools, the way an
experienced fraud analyst would, then decide.

Work in this order:
1. Gather evidence with the lookup tools.
2. If the customer's answer would change your decision, call them with
   call_customer. Ask all your questions during that one call, one short
   question per turn, including follow-ups.
3. Hang up with end_call.
4. Propose one action with propose_action. It only executes if the analyst approves.
5. Reply with the final JSON.

Rules:
- Only state facts that a tool returned. Never invent numbers or history.
- Compare the transaction against the account's own normal behaviour.
- Consider innocent explanations (travel, a large planned purchase, a shared
  household device) as seriously as fraudulent ones.
- If the customer confirms a large transfer to a newly added beneficiary, do not
  treat that as proof it is safe: they may have been manipulated. Ask whether
  anyone contacted them or told them to move money.
- Propose the least severe action that contains the risk. You cannot act
  without analyst approval. Finish investigating before you propose an action.
- You only know what happened up to the moment of the alert.
- Confidence must be realistic: reserve values above 0.95 for cases where
  independent evidence and the customer's answer all agree.
- Never tell the customer what action the bank will take; that is decided
  after the call, by the analyst.
- Speak to the customer in {language}. Write the final JSON in English.

When you are done, reply with ONLY this JSON:
{{"disposition": "FRAUD" | "LEGITIMATE" | "UNCERTAIN",
 "confidence": 0.0-1.0,
 "suspected_pattern": "...",
 "evidence_for_fraud": ["..."],
 "evidence_against_fraud": ["..."],
 "customer_verification": "...",
 "action_taken": "...",
 "summary": "two or three sentences for the analyst"}}"""


def _run_tool(name, args):
    EMIT({"type": "tool", "name": name, "args": args})
    try:
        return str(TOOLS[name](**args))
    except Exception as e:  # let the model see the error and retry
        return f"Tool error: {e}"


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _check_local() -> None:
    """Refuse to run if the model would not stay on this machine."""
    host = urlparse(OLLAMA_HOST if "://" in OLLAMA_HOST else "http://" + OLLAMA_HOST).hostname
    if host not in LOCAL_HOSTS:
        sys.exit(f"OLLAMA_HOST is {OLLAMA_HOST}. Transaction data may only go to a model "
                 "on this machine, so point it at localhost.")
    if "cloud" in MODEL.lower():
        sys.exit(f"{MODEL} is an Ollama cloud model, which runs on Ollama's servers. "
                 "Use a local tag such as qwen3:8b.")


def _client():
    import ollama
    _check_local()
    client = ollama.Client(host=OLLAMA_HOST)
    try:
        info = client.show(MODEL)
    except ollama.ResponseError:
        sys.exit(f"Model {MODEL} isn't downloaded yet. Run:  ollama pull {MODEL}")
    except Exception as e:
        sys.exit(f"Can't reach Ollama at {OLLAMA_HOST} ({type(e).__name__}). Start the "
                 "Ollama app (or run `ollama serve`) and try again.")
    if info.capabilities and "tools" not in info.capabilities:
        sys.exit(f"{MODEL} doesn't support tool calling in Ollama. Use qwen3:8b or another "
                 "model tagged 'tools' on ollama.com/library.")
    return client


OLLAMA_TOOLS = [{"type": "function",
                 "function": {"name": t["name"], "description": t["description"],
                              "parameters": t["input_schema"]}} for t in TOOL_SPECS]


def investigate_local(txn_id: str, system: str, max_steps: int = 20) -> str:
    client = _client()
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": f"Alert raised on transaction {txn_id}. Investigate it."}]
    options = {"temperature": 0, "seed": SEED, "num_ctx": NUM_CTX}
    t_in = t_out = 0
    nudged_action = nudged_json = False
    for _ in range(max_steps):
        r = client.chat(model=MODEL, messages=msgs, tools=OLLAMA_TOOLS,
                        think=THINK, options=options)
        t_in += r.prompt_eval_count or 0
        t_out += r.eval_count or 0
        m = r.message
        msgs.append(m)  # keeps the tool calls (and reasoning, if AGENT_THINK=1)
        if m.tool_calls:
            for c in m.tool_calls:
                args = dict(c.function.arguments or {})
                msgs.append({"role": "tool", "tool_name": c.function.name,
                             "content": _run_tool(c.function.name, args)})
            continue
        text = m.content or ""
        # Small models sometimes stop early. Give each slip one correction, no more.
        if not ACTION_LOG and not nudged_action:
            nudged_action = True
            msgs.append({"role": "user", "content": "You have not proposed an action yet. "
                         "Finish the investigation and call propose_action before the final JSON."})
            continue
        if parse_result(text) is None and not nudged_json:
            nudged_json = True
            msgs.append({"role": "user", "content": "Reply with only the final JSON object."})
            continue
        EMIT({"type": "tokens", "in": t_in, "out": t_out})
        return text
    return "Step limit reached without a decision."


def investigate(txn_id: str, language: str = "English") -> str:
    """Run one investigation. Returns the agent's final text (normally JSON)."""
    global ALERT_TIME, CALL_STATE
    ACTION_LOG.clear()
    CALL_STATE = "none"
    ALERT_TIME = DF.loc[txn_id, "timestamp"] if txn_id in DF.index else None
    system = SYSTEM_TEMPLATE.format(language=language)
    out = investigate_local(txn_id, system).strip()
    if CALL_STATE == "active":  # the agent forgot to hang up
        end_call()
    if out.startswith("```"):  # remove code-block markers around the JSON
        out = out.strip("`").removeprefix("json").strip()
    return out


def parse_result(text: str):
    """The case file as a dict, or None if the model did not return valid JSON."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        return json.loads(text[start:end])
    except ValueError:
        return None


def pick_df() -> pd.DataFrame:
    """Test alerts: one per fraud type, plus legitimate look-alikes."""
    rng = 7
    fraud = DF[DF.is_fraud == 1].groupby("fraud_type").sample(1, random_state=rng)
    foreign_ecom = DF[(DF.is_fraud == 0) & DF.channel.isin(["VISA_MC_ECOM", "MADA_ECOM"])
                      & (DF.merchant_country != "SA") & (DF.ip_country != "SA")]
    new_benef = DF[(DF.is_fraud == 0) & (DF.channel == "IPS_SARIE")
                   & (DF.is_new_counterparty == 1) & (DF.amount_sar >= 10000)]
    legit = pd.concat([foreign_ecom.sample(min(2, len(foreign_ecom)), random_state=rng),
                       new_benef.sample(min(2, len(new_benef)), random_state=rng)])
    return pd.concat([fraud, legit])[["txn_id", "timestamp", "amount_sar", "channel",
                                      "merchant_name", "is_fraud", "fraud_type"]]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    elif sys.argv[1] == "pick":
        print(pick_df().drop(columns=["timestamp", "merchant_name"]).to_string(index=False))
    else:
        tid = sys.argv[1]
        print(investigate(tid))
        print("\nAudit log:", json.dumps(ACTION_LOG, indent=2))
        truth = DF.loc[tid, LABELS] if tid in DF.index else "unknown"
        print("\nGround truth (hidden from agent):", dict(truth) if tid in DF.index else truth)
