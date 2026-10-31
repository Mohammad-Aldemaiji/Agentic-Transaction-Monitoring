"""Pinned E05 inference for exact, held-out synthetic dataset replays.

The live account-window contract cannot reconstruct E05's 90-day/device features.
Unknown or mismatched input fails closed rather than silently filling those gaps.
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Any

from atm_backend.adapters import Score, ScoringUnavailable
from atm_backend.models import Transaction
from atm_backend.settings import Settings

MODEL_FILES = ("fraud_model_e05.json", "model_columns.json", "model_settings.json")
CHANNELS = {
    "MADA_ECOM": "ONLINE",
    "VISA_MC_ECOM": "ONLINE",
    "SADAD_BILL": "BILL_PAYMENT",
    "CASH_DEPOSIT_BRANCH": "CASH",
    "CASH_DEPOSIT_CDM": "CASH",
    "CASH_WITHDRAWAL": "CASH",
    "WALLET_TOPUP": "WALLET",
    "RTGS_SARIE": "TRANSFER",
    "PAYROLL_CREDIT": "PAYROLL",
    "EXCHANGE_HOUSE_REMIT": "TRANSFER",
    "BNPL_SETTLEMENT": "TRANSFER",
    "SWIFT_OUTWARD": "TRANSFER",
    "MADA_POS": "CARD",
    "ATM": "ATM",
    "IPS_SARIE": "TRANSFER",
    "SARIE": "TRANSFER",
    "SWIFT": "TRANSFER",
    "SADAD": "TRANSFER",
    "WPS": "TRANSFER",
    "EXCHANGE_REMIT": "TRANSFER",
}
TEST_START = datetime.fromisoformat("2026-08-11T00:00:00+03:00")


def model_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for name in MODEL_FILES:
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256((directory / name).read_bytes()).digest())
    return digest.hexdigest()


def replay_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Explicit mapping from the research dataset to the application intake contract."""
    channel = CHANNELS.get(str(record["channel"]))
    if channel is None or record.get("currency") != "SAR":
        raise ScoringUnavailable("Unsupported sample currency or channel")
    return {
        "external_id": str(record["txn_id"]),
        "account_id": str(record["account_id"]),
        "amount_minor": int(Decimal(str(record["amount_sar"])) * 100),
        "currency": "SAR",
        "channel": channel,
        "occurred_at": str(record["timestamp"]),
    }


class E05Scorer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any = None
        self._rows: Any = None
        self._lock = Lock()

    def _load(self) -> None:
        cfg = self.settings
        if not cfg.e05_model_dir or not cfg.e05_data_file:
            raise ScoringUnavailable("E05 artifacts not configured")
        if model_digest(cfg.e05_model_dir) != cfg.scoring_model_digest:
            raise ScoringUnavailable("E05 model identity mismatch")
        if hashlib.sha256(cfg.e05_data_file.read_bytes()).hexdigest() != cfg.e05_data_digest:
            raise ScoringUnavailable("E05 dataset identity mismatch")
        import pandas as pd

        from atm_backend.e05_model import FraudModel

        rows = pd.read_csv(cfg.e05_data_file, low_memory=False)
        if rows["txn_id"].duplicated().any():
            raise ScoringUnavailable("Duplicate sample identity")
        text_fields = rows.select_dtypes(exclude="number").columns
        self._model = FraudModel(cfg.e05_model_dir, text_fields=text_fields)
        self._rows = rows.set_index("txn_id", drop=False)

    def score(self, transaction: Transaction, features: dict[str, Any]) -> Score:
        with self._lock:
            try:
                if self._model is None:
                    self._load()
                row = self._rows.loc[transaction.external_id]
                record = json.loads(row.to_json())
                expected = replay_payload(record)
                timestamp = datetime.fromisoformat(expected["occurred_at"])
                if (
                    (timestamp < TEST_START and self.settings.environment != "booth")
                    or record.get("status") != "APPROVED"
                    or transaction.occurred_at != timestamp
                    or any(
                        getattr(transaction, key) != expected[key]
                        for key in (
                            "external_id",
                            "account_id",
                            "amount_minor",
                            "currency",
                            "channel",
                        )
                    )
                    or bool(transaction.context)
                ):
                    raise ScoringUnavailable("Transaction does not match an eligible E05 sample")
                result = self._model.score(record)
                # His log-scaled score is a ranking, not a calibrated probability.
                score = Score(
                    False,
                    "fraud_e05",
                    float(result["probability"]),
                    bool(result["is_alert"]),
                    [r["label"] for r in result["reasons"]],
                    supervised_probability=float(result["probability"]),
                    anomaly_score=None,
                    contributions=result["reasons"],
                    model_digest=self.settings.scoring_model_digest,
                    metadata={
                        "score_kind": "uncalibrated_supervised_output",
                        "score_definition": "XGBoost output x 100; not calibrated certainty",
                        "alert_probability_threshold": self._model.threshold,
                        "booth_replay": self.settings.environment == "booth",
                        "evaluation_eligible": timestamp >= TEST_START,
                        "feature_source": "pinned_synthetic_e05",
                        "dataset_digest": self.settings.e05_data_digest,
                    },
                )
                return score
            except ScoringUnavailable:
                raise
            except Exception as error:
                raise ScoringUnavailable("E05 sample inference unavailable") from error
