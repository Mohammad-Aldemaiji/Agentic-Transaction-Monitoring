"""Container health probes; do not log credentials or database exceptions."""

import sys
import time
from pathlib import Path

import httpx

# Private container tmpfs, owned by its single unprivileged application user.
HEARTBEAT = Path("/tmp/atm-worker-heartbeat")  # nosec B108


def main() -> None:
    try:
        if sys.argv[1] == "worker":
            age = time.monotonic() - float(HEARTBEAT.read_text())
            if not 0 <= age < 60:
                raise ValueError("stale heartbeat")
        else:
            with httpx.Client(trust_env=False, timeout=12) as client:
                response = client.get("http://localhost:8000/health/ready")
                if response.status_code != 200:
                    raise ValueError("not ready")
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
