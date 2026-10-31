"""Optional Piper/Whisper with local weights and bounded subprocess execution.

Adapted from Mohammad's local voice approach, including PyAV decoding before Whisper.
Transcription returns a draft, never a decision. Requests never download weights.
"""

import io
import subprocess  # nosec B404
import sys
import wave
from pathlib import Path
from threading import BoundedSemaphore

from atm_backend.voice import VoiceUnavailable

VOICES = {"en": "en_US-lessac-medium", "ar": "ar_JO-kareem-medium"}
_VOICE_SLOT = BoundedSemaphore(1)


def run_voice(operation: str, directory: Path, language: str, content: bytes) -> bytes:
    if language not in VOICES or operation not in {"speak", "transcribe"}:
        raise VoiceUnavailable("Unsupported local voice operation")
    if not directory.is_dir() or not content or len(content) > 65536:
        raise VoiceUnavailable("Missing local weights or oversized input")
    if not _VOICE_SLOT.acquire(blocking=False):
        raise VoiceUnavailable("Local voice is busy")
    try:
        result = subprocess.run(  # nosec B603
            [
                sys.executable,
                "-m",
                "atm_backend.local_voice",
                operation,
                str(directory.resolve()),
                language,
            ],
            input=content,
            capture_output=True,
            check=True,
            timeout=10 if operation == "speak" else 45,
        )
    except (OSError, subprocess.SubprocessError):
        raise VoiceUnavailable("Local voice unavailable; text remains available") from None
    finally:
        _VOICE_SLOT.release()
    if len(result.stdout) > 1048576 or (
        operation == "speak" and not result.stdout.startswith(b"RIFF")
    ):
        raise VoiceUnavailable("Oversized local voice result")
    return result.stdout


def main() -> None:
    operation, directory, language = sys.argv[1:]
    if language not in VOICES:
        raise ValueError("Unsupported language")
    content = sys.stdin.buffer.read(65537)
    if not content or len(content) > 65536:
        raise ValueError("Oversized input")
    if operation == "speak":
        from piper import PiperVoice

        voice = PiperVoice.load(str(Path(directory) / (VOICES[language] + ".onnx")))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            voice.synthesize_wav(content.decode("utf-8"), wav)
        sys.stdout.buffer.write(output.getvalue())
    elif operation == "transcribe":
        import av
        import numpy as np
        from faster_whisper import WhisperModel

        chunks = []
        samples = 0
        with av.open(io.BytesIO(content), mode="r") as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunk = converted.to_ndarray().reshape(-1)
                    samples += len(chunk)
                    if samples > 15 * 16000:
                        raise ValueError("Recording exceeds 15 seconds")
                    chunks.append(chunk)
            for converted in resampler.resample(None):
                chunk = converted.to_ndarray().reshape(-1)
                samples += len(chunk)
                if samples > 15 * 16000:
                    raise ValueError("Recording exceeds 15 seconds")
                chunks.append(chunk)
        if not chunks:
            raise ValueError("Empty audio")
        model = WhisperModel(directory, device="cpu", compute_type="int8", local_files_only=True)
        audio = np.concatenate(chunks).astype("float32") / 32768
        segments, _ = model.transcribe(audio, language=language, beam_size=1, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments)
        if len(text) > 2000:
            raise ValueError("Transcript exceeds limit")
        sys.stdout.write(text)
    else:
        raise ValueError("Unsupported operation")


if __name__ == "__main__":
    main()
