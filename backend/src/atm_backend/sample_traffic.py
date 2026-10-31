"""Opt-in demo traffic: only pinned synthetic samples, scoped to the caller's team."""

import csv
import hashlib
import io
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from atm_backend.contracts import SampleTrafficInput, TransactionInput
from atm_backend.e05 import CHANNELS, TEST_START, replay_payload
from atm_backend.security import Actor, Problem
from atm_backend.services import _ingest_many
from atm_backend.settings import Settings


def replay(s: Session, a: Actor, settings: Settings, body: SampleTrafficInput) -> int:
    a.require("analyst", "supervisor", "admin")
    if not settings.sample_traffic_enabled or not settings.e05_data_file:
        raise Problem(404, "sample_traffic_disabled")
    try:
        data = settings.e05_data_file.read_bytes()
    except OSError:
        raise Problem(503, "sample_traffic_unavailable") from None
    if hashlib.sha256(data).hexdigest() != settings.e05_data_digest:
        raise Problem(503, "sample_traffic_unavailable")
    samples = []
    for row in csv.DictReader(io.StringIO(data.decode())):
        if (
            row["currency"] == "SAR"
            and row["channel"] in CHANNELS
            and row["status"] == "APPROVED"
            and datetime.fromisoformat(row["timestamp"]) >= TEST_START
        ):
            payload = TransactionInput(**replay_payload(row))
            samples.append((payload, "sample-e05-" + payload.external_id))
            if len(samples) == 50:
                break
    if len(samples) != 50:
        raise Problem(503, "sample_traffic_unavailable")
    return len(_ingest_many(s, a, samples[body.offset : body.offset + 10]))


def evaluation(a: Actor, settings: Settings) -> dict[str, Any]:
    a.require("analyst", "supervisor", "admin")
    if not settings.sample_traffic_enabled or not settings.evaluation_report_file:
        raise Problem(404, "sample_traffic_disabled")
    try:
        path = settings.evaluation_report_file
        if path.stat().st_size > 5_000_000:
            raise ValueError("Oversized report")
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or (
            report.get("model_digest") != settings.scoring_model_digest
            or report.get("data_digest") != settings.e05_data_digest
        ):
            raise ValueError("Report does not match configured artifacts")
        return report
    except (OSError, ValueError):
        raise Problem(503, "evaluation_report_unavailable") from None
