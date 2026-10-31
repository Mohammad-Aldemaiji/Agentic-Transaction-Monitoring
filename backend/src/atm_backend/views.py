"""Explicit response fields; ORM internals never become API payloads."""

from typing import Any


def fields(row: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names}


def transaction(row: Any) -> dict[str, Any]:
    return fields(
        row,
        "id",
        "external_id",
        "account_id",
        "amount_minor",
        "currency",
        "occurred_at",
        "received_at",
        "channel",
        "status",
        "result",
        "context",
        "features",
    )


def case(row: Any) -> dict[str, Any]:
    return fields(row, "id", "transaction_id", "state", "version", "assigned_to", "created_at")


def call(row: Any) -> dict[str, Any]:
    return {
        **fields(row, "id", "state", "language", "version", "expires_at"),
        "simulation": True,
        "voice_mode": "text",
        "external_audio_enabled": False,
    }


def proposal(row: Any) -> dict[str, Any]:
    return {
        **fields(
            row,
            "id",
            "case_id",
            "action",
            "target",
            "reason",
            "state",
            "version",
            "evidence_version",
            "proposer_id",
            "approver_id",
            "expires_at",
        ),
        "simulation": True,
    }


def evidence(row: Any) -> dict[str, Any]:
    return fields(
        row,
        "id",
        "external_id",
        "account_id",
        "amount_minor",
        "currency",
        "occurred_at",
        "channel",
        "context",
    )
