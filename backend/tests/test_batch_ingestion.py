"""Real PostgreSQL batch bounds, atomicity and overlapping retry regression."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event, func, select

from atm_backend.models import Audit, Job, Transaction


def send(env, items):
    return env["client"].post(
        "/api/v1/transactions/batch", json={"items": items}, headers=env["headers"]("ingestion")
    )


def test_maximum_batch_has_bounded_round_trips_and_ordered_retries(env, transaction_body):
    items = [
        {**transaction_body, "external_id": f"batch-{i}", "idempotency_key": f"key-{i}"}
        for i in range(100)
    ]
    statements = []
    engine = env["runtime"].kw["bind"]

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    started = time.monotonic()
    try:
        response = send(env, items)
    finally:
        elapsed = time.monotonic() - started
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 202
    rows = response.json()["items"]
    assert len({r["id"] for r in rows}) == 100
    assert [r["external_id"] for r in rows] == [i["external_id"] for i in items]
    # Includes authorization/rate limiting, batch locking, lookups, and writes.
    # A regression to per-item locks/inserts exceeds this by hundreds of queries.
    assert len(statements) <= 12
    retry = send(env, list(reversed(items)))
    assert retry.status_code == 202
    assert [r["id"] for r in retry.json()["items"]] == [r["id"] for r in reversed(rows)]
    with env["factory"]() as s:
        for model in (Transaction, Job, Audit):
            assert s.scalar(select(func.count()).select_from(model)) == 100
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "scope": "100 synthetic transactions, test authentication, real Supabase atm_test; no ML",
        "transactions": 100,
        "database_statements": len(statements),
        "acceptance_seconds": elapsed,
        "acceptance_transactions_per_second": 100 / elapsed,
        "retry_durable_counts": {"transactions": 100, "jobs": 100, "audit": 100},
        "limitations": "One bounded batch; not sustained throughput or end-to-end scoring latency",
    }
    target = Path(__file__).resolve().parents[2] / "docs/evidence/batch-performance.json"
    target.write_text(json.dumps(report, indent=2) + "\n")


def test_overlapping_batches_and_single_request_share_retry_locks(env, transaction_body):
    first = {**transaction_body, "external_id": "first", "idempotency_key": "first"}
    second = {**transaction_body, "external_id": "second", "idempotency_key": "second"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(send, env, [first, second]),
            pool.submit(send, env, [second, first]),
            pool.submit(
                env["client"].post,
                "/api/v1/transactions",
                json={k: v for k, v in first.items() if k != "idempotency_key"},
                headers={**env["headers"]("ingestion"), "Idempotency-Key": "first"},
            ),
        ]
        responses = [future.result(timeout=30) for future in futures]
    assert [r.status_code for r in responses] == [202, 202, 202]
    assert responses[0].json()["items"][0]["id"] == responses[2].json()["id"]
    with env["factory"]() as s:
        for model in (Transaction, Job, Audit):
            assert s.scalar(select(func.count()).select_from(model)) == 2


def test_batch_internal_conflicts_are_atomic_and_identical_duplicates_collapse(
    env, transaction_body
):
    first = {**transaction_body, "idempotency_key": "first"}
    for conflict in (
        {**first, "amount_minor": 99},
        {**first, "idempotency_key": "different"},
        {**first, "external_id": "different"},
    ):
        assert send(env, [first, conflict]).status_code == 409
        with env["factory"]() as s:
            for model in (Transaction, Job, Audit):
                assert s.scalar(select(func.count()).select_from(model)) == 0
    response = send(env, [first, first])
    assert response.status_code == 202
    assert response.json()["items"][0]["id"] == response.json()["items"][1]["id"]
    with env["factory"]() as s:
        for model in (Transaction, Job, Audit):
            assert s.scalar(select(func.count()).select_from(model)) == 1
