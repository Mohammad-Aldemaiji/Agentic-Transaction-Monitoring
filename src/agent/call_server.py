"""
Voice-call demo for the fraud investigation agent. Everything runs locally.

    pip install -r requirements.txt
    python -m piper.download_voices en_US-lessac-medium ar_JO-kareem-medium --download-dir voices
    python call_server.py
    then open  http://localhost:8000  in Microsoft Edge or Google Chrome

The agent logic is the same as in fraud_agent.py. This file only replaces the
terminal with a browser: an analyst console on the left and a phone on the right.

    Agent         Ollama on localhost (see fraud_agent.py)
    Agent voice   Piper text-to-speech, voice files in ./voices
    Customer      faster-whisper speech-to-text on this machine

The page records the microphone and sends the clip to this server, not to the
browser's own speech recognition (Chrome's and Edge's send audio to their cloud).
Downloads happen once (Piper voices, the Whisper model); after that it runs offline.
"""
import hashlib
import io
import os
import threading
import traceback
import wave
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import fraud_agent as fa

HERE = Path(__file__).resolve().parent
app = FastAPI()

# ------------------------------------------------------------------ voice
VOICE_DIR = HERE / "voices"
VOICES = {"English": "en_US-lessac-medium", "Arabic": "ar_JO-kareem-medium"}
TTS_DIR = HERE / "tts_cache"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")      # "medium" is better for Arabic, slower
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")       # "cuda" if CUDA 12 + cuDNN are installed

LINES = {
    "English": {
        "intro": "Hello, this is the fraud prevention team at your bank, calling about "
                 "recent activity on your account. We will never ask for your PIN, "
                 "one-time password or card number. If you are not sure this call is "
                 "genuine, hang up and call the number on the back of your card.",
        "bye": "Thank you for your time. The bank will follow up with you shortly. Goodbye.",
        "code": "en-US", "whisper": "en",
    },
    "Arabic": {
        "intro": "السلام عليكم، معك فريق مكافحة الاحتيال في البنك، نتصل بخصوص عمليات حديثة "
                 "على حسابك. لن نطلب منك أبداً الرقم السري أو رمز التحقق أو رقم البطاقة. "
                 "إذا لم تكن متأكداً أن هذه المكالمة من البنك، أغلق الخط واتصل بالرقم "
                 "الموجود خلف بطاقتك.",
        "bye": "شكراً لوقتك، سيتواصل معك البنك قريباً. مع السلامة.",
        "code": "ar-SA", "whisper": "ar",
    },
}
_voices = {}
_tts_lock = threading.Lock()
_whisper = None
_whisper_lock = threading.Lock()


def _voice(language: str):
    """Load the Piper voice for a language once, or return None if it isn't downloaded."""
    if language not in _voices:
        from piper import PiperVoice
        path = VOICE_DIR / f"{VOICES[language]}.onnx"
        _voices[language] = PiperVoice.load(path) if path.exists() else None
    return _voices[language]


def tts(text: str):
    """Return a URL for a WAV of `text` in the local Piper voice, or None to fall
    back to the browser's offline voice."""
    name = VOICES[S.language]
    key = hashlib.sha1(f"piper|{name}|{text}".encode()).hexdigest()[:20]
    path = TTS_DIR / f"{key}.wav"
    with _tts_lock:  # one synthesis at a time; also stops duplicate work
        if path.exists():
            return f"/api/audio/{key}.wav"
        if S.voice_down:
            return None
        try:
            voice = _voice(S.language)
            if voice is None:
                raise FileNotFoundError(f"voices/{name}.onnx is missing. Download it with: "
                                        f"python -m piper.download_voices {name} --download-dir voices")
            TTS_DIR.mkdir(exist_ok=True)
            with wave.open(str(path), "wb") as w:
                voice.synthesize_wav(text, w)
        except Exception as e:
            path.unlink(missing_ok=True)
            S.voice_down = True
            S.emit({"type": "notice", "text": f"Piper voice unavailable, using the browser's "
                                              f"offline voice ({type(e).__name__}: {str(e)[:160]})"})
            return None
    return f"/api/audio/{key}.wav"


def transcribe(audio: bytes, language: str) -> str:
    """Speech to text with faster-whisper, on this machine."""
    global _whisper
    from faster_whisper import WhisperModel
    with _whisper_lock:
        if _whisper is None:
            compute = "int8" if WHISPER_DEVICE == "cpu" else "float16"
            _whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=compute)
        segments, _ = _whisper.transcribe(io.BytesIO(audio), language=LINES[language]["whisper"],
                                          beam_size=5, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def line(kind: str) -> dict:
    text = LINES[S.language][kind]
    return {"text": text, "audio": tts(text)}


class Session:
    """State of the one investigation that is running."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []          # everything the page needs to draw, in order
        self.running = False
        self.language = "English"
        self.in_call = False
        self.voice_down = False   # after one Piper failure, stop retrying this run
        self._reply = None        # (threading.Event, [value]) the agent is waiting on

    def emit(self, event: dict) -> None:
        with self.lock:
            event["seq"] = len(self.events)
            self.events.append(event)

    def wait_for(self, event: dict):
        """Show something in the page, then block the agent until the page answers."""
        done, box = threading.Event(), [None]
        with self.lock:
            self._reply = (done, box)
        self.emit(event)
        if not done.wait(timeout=600):
            raise TimeoutError("No response from the browser within 10 minutes.")
        return box[0]

    def answer(self, value) -> bool:
        with self.lock:
            if self._reply is None:
                return False
            done, box = self._reply
            self._reply = None
        box[0] = value
        done.set()
        return True


S = Session()


HANGUPS = {"__DECLINED__": "(The customer did not answer the call.)",
           "__HANGUP__": "(The customer hung up.)"}


def web_ask_customer(question: str) -> str:
    event = {"type": "call_turn", "text": question, "audio": tts(question),
             "code": LINES[S.language]["code"]}
    if not S.in_call:
        event["intro"] = line("intro")
    reply = S.wait_for(event)
    if reply in HANGUPS:
        fa.CALL_STATE = "ended"
        S.in_call = False
        S.emit({"type": "notice", "text": HANGUPS[reply]})
        return HANGUPS[reply]
    S.in_call = True
    S.emit({"type": "customer_said", "text": reply})
    return reply


def web_end_call() -> None:
    S.in_call = False
    S.emit({"type": "call_end", "bye": line("bye")})


def web_ask_approval(action: str, reason: str) -> bool:
    ok = bool(S.wait_for({"type": "approval", "action": action, "reason": reason}))
    S.emit({"type": "approval_result", "action": action, "approved": ok})
    return ok


fa.ASK_CUSTOMER = web_ask_customer
fa.ASK_APPROVAL = web_ask_approval
fa.END_CALL = web_end_call
fa.EMIT = S.emit


def run_investigation(txn_id: str, language: str) -> None:
    # make the fixed lines ahead of time so the call starts without a pause
    threading.Thread(target=lambda: [tts(LINES[language][k]) for k in ("intro", "bye")],
                     daemon=True).start()
    try:
        S.emit({"type": "started", "txn_id": txn_id, "provider": fa.PROVIDER,
                "model": fa.MODEL, "transaction": _txn_card(txn_id)})
        text = fa.investigate(txn_id, language)
        S.emit({"type": "final", "text": text, "case": fa.parse_result(text),
                "audit": list(fa.ACTION_LOG)})
    except Exception as e:
        traceback.print_exc()
        S.emit({"type": "error", "text": f"{type(e).__name__}: {e}"})
    finally:
        row = fa.DF.loc[txn_id]
        S.emit({"type": "truth", "is_fraud": int(row["is_fraud"]),
                "fraud_type": None if row["fraud_type"] != row["fraud_type"] else row["fraud_type"]})
        S.running = False


def _txn_card(txn_id: str) -> dict:
    r = fa.DF.loc[txn_id]
    return {"txn_id": txn_id, "time": str(r["timestamp"])[:16],
            "amount_sar": round(float(r["amount_sar"]), 2), "channel": r["channel"],
            "merchant": r["merchant_name"] if isinstance(r["merchant_name"], str) else "",
            "city": r["txn_city"], "account": r["account_id"]}


# ------------------------------------------------------------------ routes

@app.get("/")
def page():
    return FileResponse(HERE / "call_demo.html")


@app.get("/api/audio/{name}")
def audio(name: str):
    path = TTS_DIR / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/transcribe")
async def stt(request: Request):
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "No audio received.")
    try:
        return {"text": await run_in_threadpool(transcribe, audio, S.language)}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Speech recognition failed: {type(e).__name__}: {e}")


@app.get("/api/alerts")
def alerts():
    df = fa.pick_df()
    return [{"txn_id": r.txn_id, "time": str(r.timestamp)[:16],
             "amount_sar": round(float(r.amount_sar), 2), "channel": r.channel,
             "merchant": r.merchant_name if isinstance(r.merchant_name, str) else "",
             "answer_key": r.fraud_type if r.is_fraud else "LEGITIMATE"}
            for r in df.itertuples()]


class Start(BaseModel):
    txn_id: str
    language: str = "English"


@app.post("/api/start")
def start(req: Start):
    txn_id = req.txn_id.strip()
    if S.running:
        raise HTTPException(409, "An investigation is already running.")
    if txn_id not in fa.DF.index:
        raise HTTPException(404, f"Transaction {txn_id} not found.")
    with S.lock:
        S.events.clear()
        S._reply = None
    S.language = req.language if req.language in LINES else "English"
    S.in_call = False
    S.voice_down = False
    S.running = True
    threading.Thread(target=run_investigation, args=(txn_id, req.language),
                     daemon=True).start()
    return {"ok": True}


@app.get("/api/events")
def events(since: int = 0):
    with S.lock:
        return {"events": S.events[since:], "running": S.running}


class Reply(BaseModel):
    value: str | bool


@app.post("/api/reply")
def reply(r: Reply):
    if not S.answer(r.value):
        raise HTTPException(409, "The agent is not waiting for anything.")
    return {"ok": True}


if __name__ == "__main__":
    print("\n  Open http://localhost:8000 in Edge or Chrome\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
