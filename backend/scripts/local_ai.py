"""Run the merged native API/dashboard or worker with existing restricted credentials.

API: https://localhost:8444. Worker: stop the old worker before starting this one.
This enables providers, never grants model approval or bypasses action approval.
"""

import argparse
import hashlib
import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from atm_backend.e05 import model_digest
from atm_backend.local_app import create_app
from atm_backend.settings import Settings

root = Path(__file__).resolve().parents[2]
runtime = root / ".runtime"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("service", choices=["api", "worker"])
args = parser.parse_args()
values = dotenv_values(runtime / "supabase-app.env")
if not values.get("ATM_DATABASE_URL"):
    parser.error("Existing restricted Supabase application configuration is required")
connection = make_url(str(values["ATM_DATABASE_URL"]))
if not (connection.username or "").startswith("atm_app."):
    parser.error("Use the existing restricted application login")
connection = connection.update_query_dict({"sslrootcert": str(runtime / "supabase-ca.pem")})
values["ATM_DATABASE_URL"] = connection.render_as_string(hide_password=False)
data, model = root / "data/sample/transactions_mini.csv", root / "models/fraud_e05"
values.update(
    {
        "ATM_BROWSER_ORIGIN": "https://localhost:8444",
        "ATM_ALLOWED_HOSTS": '["localhost"]',
        "ATM_METRICS_ENABLED": "false",
        "ATM_SCORING_MODE": "e05_sample",
        "ATM_SCORING_MODEL_NAME": "fraud_e05",
        "ATM_SCORING_MODEL_DIGEST": model_digest(model),
        "ATM_E05_MODEL_DIR": str(model),
        "ATM_E05_DATA_FILE": str(data),
        "ATM_E05_DATA_DIGEST": hashlib.sha256(data.read_bytes()).hexdigest(),
        "ATM_AGENT_ENABLED": "true",
        "ATM_SAMPLE_TRAFFIC_ENABLED": "true",
        "ATM_EVALUATION_REPORT_FILE": str(runtime / "e05-evaluation.json"),
        "ATM_AGENT_URL": "http://127.0.0.1:11434",
        "ATM_AGENT_MODEL": os.environ.get("ATM_AGENT_MODEL", "qwen3:4b"),
    }
)
voices, whisper = runtime / "local-ai/voices", runtime / "local-ai/whisper-small"
if all(
    (voices / (name + ".onnx")).is_file() for name in ("en_US-lessac-medium", "ar_JO-kareem-medium")
):
    values["ATM_PIPER_VOICE_DIR"] = str(voices)
if (whisper / "model.bin").is_file():
    values["ATM_WHISPER_MODEL_DIR"] = str(whisper)
os.environ.update({key: value for key, value in values.items() if value is not None})
settings = Settings()
if args.service == "worker":
    from atm_backend.worker import main

    main()
else:
    if not (root / "frontend/dist/index.html").is_file():
        parser.error("Build the frontend before starting the local profile")
    uvicorn.run(
        create_app(settings, root / "frontend/dist"),
        host="127.0.0.1",
        port=8444,
        ssl_certfile=str(runtime / "localhost.pem"),
        ssl_keyfile=str(runtime / "localhost-key.pem"),
        access_log=False,
        proxy_headers=False,
        limit_concurrency=30,
    )
