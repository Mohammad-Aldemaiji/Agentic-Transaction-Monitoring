"""One entry point for the local senior-project backend demonstration."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "verify"))
    args = parser.parse_args()
    if args.action == "start":
        if not (ROOT / "frontend/node_modules").is_dir():
            run("npm", "--prefix", "frontend", "ci", "--ignore-scripts")
        run("npm", "--prefix", "frontend", "run", "build")
        run(sys.executable, "backend/scripts/prepare_local_demo.py")
        run("docker", "compose", "up", "-d", "--build", "--wait", "--wait-timeout", "120")
        print("Workspace: https://localhost:8443 | System health: https://localhost:9443")
    elif args.action == "stop":
        run("docker", "compose", "stop")
        print("Local services stopped. Supabase and saved monitoring data are preserved.")
    elif args.action == "status":
        run("docker", "compose", "ps")
    else:
        run(sys.executable, "backend/scripts/verify_local_demo.py", "--exercise-alert")


if __name__ == "__main__":
    main()
