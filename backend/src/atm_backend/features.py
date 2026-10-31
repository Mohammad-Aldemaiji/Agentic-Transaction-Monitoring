"""Versioned point-in-time features, independent of any model implementation.

Windows exclude the current transaction and observations received after it. Amount
statistics never mix currencies. Missing optional inputs remain unknown, not zero.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from atm_backend.models import Transaction

FEATURE_VERSION = "account-windows-v1"


def build_features(s: Session, transaction: Transaction) -> dict[str, Any]:
    t = transaction
    prior = [
        Transaction.team == t.team,
        Transaction.account_id == t.account_id,
        Transaction.id != t.id,
        Transaction.received_at <= t.received_at,
        Transaction.occurred_at <= t.occurred_at,
        Transaction.occurred_at >= t.occurred_at - timedelta(days=7),
    ]
    same_currency = Transaction.currency == t.currency
    expressions: list[Any] = []
    for label, duration in (
        ("1h", timedelta(hours=1)),
        ("24h", timedelta(days=1)),
        ("7d", timedelta(days=7)),
    ):
        window = Transaction.occurred_at >= t.occurred_at - duration
        expressions.extend(
            [
                func.count().filter(window).label("count_" + label),
                func.count().filter(and_(window, same_currency)).label("currency_count_" + label),
                func.coalesce(
                    func.sum(Transaction.amount_minor).filter(and_(window, same_currency)), 0
                ).label("amount_minor_" + label),
            ]
        )
    expressions.extend(
        [
            func.avg(Transaction.amount_minor).filter(same_currency).label("mean_amount_minor_7d"),
            func.stddev_pop(Transaction.amount_minor)
            .filter(same_currency)
            .label("stddev_amount_minor_7d"),
            func.max(Transaction.occurred_at).label("previous_at"),
        ]
    )
    for key in ("destination_account_id", "device_id", "location_country"):
        expressions.append(
            func.count()
            .filter(Transaction.context[key].as_string() == t.context.get(key))
            .label("seen_" + key)
        )
    row = s.execute(select(*expressions).where(*prior)).mappings().one()
    values: dict[str, Any] = {
        k: int(v)
        for k, v in row.items()
        if k.startswith(("count_", "currency_count_", "amount_minor_"))
    }
    mean, deviation = row["mean_amount_minor_7d"], row["stddev_amount_minor_7d"]
    values.update(
        {
            "mean_amount_minor_7d": float(mean) if mean is not None else None,
            "amount_zscore_7d": float((t.amount_minor - mean) / deviation) if deviation else None,
            "seconds_since_previous": (t.occurred_at - row["previous_at"]).total_seconds()
            if row["previous_at"]
            else None,
            "hour_utc": t.occurred_at.hour,
            "weekday_utc": t.occurred_at.weekday(),
        }
    )
    for key in ("destination_account_id", "device_id", "location_country"):
        values["new_" + key + "_7d"] = row["seen_" + key] == 0 if t.context.get(key) else None
    late = (
        s.scalar(
            select(Transaction.id)
            .where(
                Transaction.team == t.team,
                Transaction.account_id == t.account_id,
                Transaction.received_at <= t.received_at,
                Transaction.id != t.id,
                Transaction.occurred_at > t.occurred_at,
            )
            .limit(1)
        )
        is not None
    )
    # The same event-time boundary is used on every retry; no backfill mutates this snapshot.
    return {
        "version": FEATURE_VERSION,
        "as_of": t.occurred_at.isoformat(),
        "received_cutoff": t.received_at.isoformat(),
        "currency": t.currency,
        "cold_start": values["count_7d"] == 0,
        "late_event": late,
        "values": values,
    }
