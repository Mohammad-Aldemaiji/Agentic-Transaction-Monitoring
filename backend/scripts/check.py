"""Run quality checks and the isolated Supabase test suite."""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[2]
values = dotenv_values(root / ".runtime/test.env")
os.environ.update({key: value for key, value in values.items() if value})
commands = [
    [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
    [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts"],
    [sys.executable, "-m", "mypy", "src"],
    [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "--junitxml=test-results.xml",
        *sys.argv[1:],
    ],
]
for command in commands:
    subprocess.run(command, cwd=root / "backend", check=True)
