"""Print pinned E05 configuration or an eligible synthetic replay payload; no writes."""

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

from atm_backend.e05 import TEST_START, model_digest, replay_payload

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--transaction", help="Print the intake JSON for this test-period sample ID")
args = parser.parse_args()
data = root / "data/sample/transactions_mini.csv"
model = root / "models/fraud_e05"
if args.transaction:
    with data.open() as stream:
        record = next((r for r in csv.DictReader(stream) if r["txn_id"] == args.transaction), None)
    if (
        not record
        or record["status"] != "APPROVED"
        or datetime.fromisoformat(record["timestamp"]) < TEST_START
    ):
        parser.error("Choose an approved test-period sample")
    print(json.dumps(replay_payload(record), indent=2))
else:
    print(
        json.dumps(
            {
                "ATM_SCORING_MODE": "e05_sample",
                "ATM_SCORING_MODEL_NAME": "fraud_e05",
                "ATM_SCORING_MODEL_DIGEST": model_digest(model),
                "ATM_E05_MODEL_DIR": str(model),
                "ATM_E05_DATA_FILE": str(data),
                "ATM_E05_DATA_DIGEST": hashlib.sha256(data.read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )
