"""Reproducible sample-only threshold study and customer-history demonstration.

The incumbent weights are never changed. Candidate threshold is chosen on validation
only, before the final test is scored. No deployment or governance approval is performed.
"""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from atm_backend.e05 import CHANNELS, model_digest
from atm_backend.e05_model import FraudModel

ROOT = Path(__file__).resolve().parents[2]


def metrics(labels, scores, threshold):
    y, pred = np.asarray(labels, dtype=bool), np.asarray(scores) >= threshold
    tp, fp = int((y & pred).sum()), int((~y & pred).sum())
    fn, tn = int((y & ~pred).sum()), int((~y & ~pred).sum())
    return {
        "transactions": len(y),
        "fraud": tp + fn,
        "caught": tp,
        "missed": fn,
        "false_alarms": fp,
        "true_negatives": tn,
        "recall": tp / max(1, tp + fn),
        "precision": tp / max(1, tp + fp),
        "false_positive_rate": fp / max(1, fp + tn),
        "threshold": float(threshold),
    }


def choose_threshold(labels, scores):
    # Predeclared objective: reach >=85% recall with <=2% false positives, then
    # maximize sample precision. Never select a threshold from the test scores.
    choices = [metrics(labels, scores, t) for t in np.unique(scores)]
    allowed = [m for m in choices if m["false_positive_rate"] <= 0.02]
    target = [m for m in allowed if m["recall"] >= 0.85]
    pool = target or allowed
    if not pool:
        raise ValueError("No threshold meets the false-positive constraint")
    return max(
        pool, key=lambda m: (m["precision"] if target else m["recall"], m["recall"], m["threshold"])
    )["threshold"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/e05-evaluation.json")
    args = parser.parse_args()
    data, directory = ROOT / "data/sample/transactions_mini.csv", ROOT / "models/fraud_e05"
    frame = pd.read_csv(data, low_memory=False)
    times = pd.to_datetime(frame.timestamp, utc=True)
    eligible = (
        (frame.status == "APPROVED") & (frame.currency == "SAR") & frame.channel.isin(CHANNELS)
    )
    # Whole boundary dates excluded: the notebook only records split dates, not
    # exact split timestamps. This conservatively avoids boundary contamination.
    validation = frame[eligible & (times >= "2026-07-17") & (times < "2026-08-11")]
    test = frame[eligible & (times >= "2026-08-12")]
    model = FraudModel(directory)
    model.booster.set_param({"nthread": 2})
    val_scores = model.predict(model.prepare(validation))
    threshold = choose_threshold(validation.is_fraud, val_scores)
    test_scores = model.predict(model.prepare(test))
    baseline = metrics(test.is_fraud, test_scores, model.threshold)
    candidate = metrics(test.is_fraud, test_scores, threshold)
    report = {
        "scope": (
            "Synthetic, fraud-enriched sample; not a population estimate or deployment approval."
        ),
        "model_digest": model_digest(directory),
        "data_digest": hashlib.sha256(data.read_bytes()).hexdigest(),
        "rows": len(frame),
        "accounts": int(frame.account_id.nunique()),
        "sample_fraud": int(frame.is_fraud.sum()),
        "test_period_rows": int((times >= "2026-08-12").sum()),
        "eligibility": "Approved SAR payments on supported channels only.",
        "validation_period": "2026-07-17 through 2026-08-10 UTC",
        "test_period": "2026-08-12 through 2026-09-30 UTC",
        "policy": (
            "Validation-only threshold: recall >=85%, false-positive rate <=2%; "
            "maximize sample precision. Otherwise maximize recall below the false- "
            "positive limit."
        ),
        "validation": {
            "baseline": metrics(validation.is_fraud, val_scores, model.threshold),
            "candidate": metrics(validation.is_fraud, val_scores, threshold),
        },
        "test": {"baseline": baseline, "candidate": candidate},
        "candidate_improves_target": candidate["recall"] > baseline["recall"]
        and candidate["false_positive_rate"] <= 0.02,
        "limitations": [
            (
                "This sample has 1,305 fraud labels, the same count reported for the "
                "746,698-row source; sampling probabilities are unknown."
            ),
            (
                "Precision, alert counts and aggregate accuracy cannot be extrapolated to "
                "real traffic."
            ),
            (
                "The original training rows and exact split timestamps are unavailable; "
                "held-out status is inferred from the saved notebook dates, not "
                "independently proven."
            ),
            (
                "This study changes only the decision threshold, not the trained weights. "
                "Any further tuning requires a new untouched test set."
            ),
            (
                "Dataset history is incomplete. Precomputed behavioral features may "
                "summarize transactions absent from this sample."
            ),
            (
                "Customer demos are deliberately selected examples, not additional "
                "evaluation evidence."
            ),
        ],
        "fraud_types": {},
        "customers": [],
    }
    for kind in sorted(test.loc[test.is_fraud == 1, "fraud_type"].unique()):
        mask = (test.fraud_type == kind).to_numpy()
        report["fraud_types"][kind] = {
            "baseline": metrics(test.is_fraud[mask], test_scores[mask], model.threshold),
            "candidate": metrics(test.is_fraud[mask], test_scores[mask], threshold),
        }
    groups = frame.groupby("account_id").agg(count=("txn_id", "size"), fraud=("is_fraud", "sum"))
    has_test = set(test.account_id)
    groups = groups[groups.index.isin(has_test)]
    normal = (
        groups[groups.fraud == 0].sort_values(["count"], ascending=False).head(2).index.tolist()
    )
    suspicious = (
        groups[(groups.fraud > 0) & groups.index.isin(test.loc[test.is_fraud == 1, "account_id"])]
        .sort_values(["count"], ascending=False)
        .head(2)
        .index.tolist()
    )
    probability = dict(zip(test.txn_id, map(float, test_scores), strict=True))
    for index, account in enumerate(normal + suspicious):
        history = frame[frame.account_id == account].sort_values("timestamp")
        entries = []
        for _, row in history.iterrows():
            score = probability.get(row.txn_id)
            entries.append(
                {
                    "id": row.txn_id,
                    "timestamp": row.timestamp,
                    "amount": float(row.amount),
                    "currency": row.currency,
                    "channel": row.channel,
                    "authorization": row.status,
                    "probability": score,
                    "fraud_label": bool(row.is_fraud),
                    "baseline_alert": score >= model.threshold if score is not None else None,
                    "candidate_alert": score >= threshold if score is not None else None,
                }
            )
        report["customers"].append(
            {
                "name": f"Sample customer {index + 1}",
                "account_id": account,
                "scenario": "Ordinary activity"
                if index < 2
                else "History containing labelled fraud",
                "transactions": len(entries),
                "fraud": int(history.is_fraud.sum()),
                "evaluated": sum(e["probability"] is not None for e in entries),
                "history": entries,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "validation": report["validation"],
                "test": report["test"],
                "customers": [
                    {k: v for k, v in c.items() if k != "history"} for c in report["customers"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
