"""Bounded offline speech. No customer data goes to an external voice provider."""

import shutil

# Fixed local executable, no shell; text is passed on stdin.
import subprocess  # nosec B404


class VoiceUnavailable(Exception):
    pass


def introduction(language: str, phrase: str) -> bytes:
    if language not in {"en", "ar"} or not 1 <= len(phrase) <= 1000:
        raise VoiceUnavailable("Invalid speech input")
    executable = shutil.which("espeak-ng")
    if not executable:
        raise VoiceUnavailable("Local speech engine unavailable; text remains available")
    try:
        # Allowlisted language; text cannot become options.
        result = subprocess.run(  # nosec B603
            [executable, "--stdout", "-v", language, "-s", "145", "--stdin"],
            input=phrase.encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        raise VoiceUnavailable("Local speech generation failed") from None
    if not result.stdout.startswith(b"RIFF") or len(result.stdout) > 1048576:
        raise VoiceUnavailable("Invalid or oversized audio")
    return result.stdout
