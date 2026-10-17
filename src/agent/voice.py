"""Local voice for the verification call: Piper speaks, faster-whisper listens.

Both run on this machine. Voices download once:
    python -m piper.download_voices en_US-lessac-medium ar_JO-kareem-medium --download-dir src/agent/voices
The Whisper weights download automatically on first use. After that it works offline.
"""
import hashlib
import io
import threading
import wave

import numpy as np

from src import config

LANGUAGES = {
    "English": {
        "voice": "en_US-lessac-medium", "whisper": "en", "code": "en-US",
        "intro": "Hello, this is the fraud prevention team at your bank, calling about recent "
                 "activity on your account. We will never ask for your PIN, one-time password or "
                 "card number. If you are not sure this call is genuine, hang up and call the "
                 "number on the back of your card.",
        "bye": "Thank you for your time. The bank will follow up with you shortly. Goodbye.",
    },
    "Arabic": {
        "voice": "ar_JO-kareem-medium", "whisper": "ar", "code": "ar-SA",
        "intro": "السلام عليكم، معك فريق مكافحة الاحتيال في البنك، نتصل بخصوص عمليات حديثة على "
                 "حسابك. لن نطلب منك أبداً الرقم السري أو رمز التحقق أو رقم البطاقة. إذا لم تكن "
                 "متأكداً أن هذه المكالمة من البنك، أغلق الخط واتصل بالرقم الموجود خلف بطاقتك.",
        "bye": "شكراً لوقتك، سيتواصل معك البنك قريباً. مع السلامة.",
    },
}

_voices, _tts_lock = {}, threading.Lock()
_whisper, _stt_lock = None, threading.Lock()
_tts_error = {}


def speak(text: str, language: str):
    """Synthesize `text`; returns the cached WAV file name, or None if no voice is installed
    (the phone page then shows the text and uses an offline browser voice if it has one)."""
    lang = LANGUAGES[language]
    key = hashlib.sha1(f"piper|{lang['voice']}|{text}".encode()).hexdigest()[:20] + ".wav"
    path = config.TTS_CACHE / key
    with _tts_lock:
        if path.exists():
            return key
        if language in _tts_error:
            return None
        try:
            if language not in _voices:
                from piper import PiperVoice
                model = config.VOICE_DIR / f"{lang['voice']}.onnx"
                if not model.exists():
                    raise FileNotFoundError(f"{model.name} is missing; run: python -m piper.download_voices "
                                            f"{lang['voice']} --download-dir src/agent/voices")
                _voices[language] = PiperVoice.load(model)
            config.TTS_CACHE.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as w:
                _voices[language].synthesize_wav(text, w)
            return key
        except Exception as e:
            path.unlink(missing_ok=True)
            _tts_error[language] = f"{type(e).__name__}: {e}"
            return None


def tts_status() -> dict:
    return {lang: {"voice": v["voice"], "installed": (config.VOICE_DIR / f"{v['voice']}.onnx").exists(),
                   "error": _tts_error.get(lang)} for lang, v in LANGUAGES.items()}


def load_whisper():
    """Load (and on first use, download) the Whisper model."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        compute = "int8" if config.WHISPER_DEVICE == "cpu" else "float16"
        _whisper = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type=compute)
    return _whisper


def transcribe(audio: bytes, language: str) -> str:
    """Speech to text with faster-whisper on this machine."""
    with _stt_lock:
        load_whisper()
        segments, _ = _whisper.transcribe(decode(audio), language=LANGUAGES[language]["whisper"],
                                          beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def decode(audio: bytes) -> np.ndarray:
    """Browser recording (webm/opus, ogg, wav...) -> 16 kHz mono float32, which Whisper takes
    directly. Done here rather than by faster-whisper, whose own decoder breaks with PyAV 15+."""
    import av
    out = []
    with av.open(io.BytesIO(audio), mode="r") as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for f in resampler.resample(frame):
                out.append(f.to_ndarray().reshape(-1))
        for f in resampler.resample(None):  # flush what the resampler still holds
            out.append(f.to_ndarray().reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out).astype(np.float32) / 32768.0
