"""Paths and settings shared by the whole system. Override any of them with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Data: the full dataset if it is there, otherwise the mini sample that ships in Git.
DATA_FULL = ROOT / "data" / "transactions.csv"
DATA_SAMPLE = ROOT / "data" / "sample" / "transactions_mini.csv"
DATA_FILE = Path(os.environ.get("DATA_FILE", DATA_FULL if DATA_FULL.exists() else DATA_SAMPLE))

MODEL_DIR = ROOT / "models" / "fraud_e05"

# Start of the test period (the last 20% of the data by time). Replay starts here so the
# demo only streams transactions the model never trained on.
TEST_START = os.environ.get("TEST_START", "2026-08-11")

# Database: SQLite file by default; set DATABASE_URL to use PostgreSQL, e.g.
# postgresql+psycopg://atm:atm@localhost:5432/atm
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'monitoring.db'}")

# Local language model (see src/agent/llm.py)
AGENT_MODEL = os.environ.get("AGENT_MODEL", "qwen3:8b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
AGENT_NUM_CTX = int(os.environ.get("AGENT_NUM_CTX", "8192"))
AGENT_THINK = os.environ.get("AGENT_THINK", "0") == "1"

# Local voice (see src/agent/voice.py)
VOICE_DIR = ROOT / "src" / "agent" / "voices"
TTS_CACHE = ROOT / "data" / "tts_cache"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")

DASHBOARD_DIST = ROOT / "dashboard" / "dist"
