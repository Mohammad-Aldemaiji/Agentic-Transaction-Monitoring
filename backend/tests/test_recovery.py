from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_workflows import case_for, post, submit

from atm_backend.adapters import SimulationScorer
from atm_backend.models import Case, Execution, Job, Proposal, Transaction
from atm_backend.security import Problem
from atm_backend.worker import process, run_one


def test_masked_ping_disconnect_reconnects_before_request(env, transaction_body, monkeypatch):
    from psycopg import OperationalError, ProgrammingError

    engine = env["app"].state.engine
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    original_ping = engine.dialect.do_ping
    failures = []

    def masked_ping(connection):
        if not failures:
            failures.append(True)
            try:
                raise OperationalError("simulated socket failure")
            except OperationalError:
                raise ProgrammingError("simulated autocommit restoration failure") from None
        return original_ping(connection)

    monkeypatch.setattr(engine.dialect, "do_ping", masked_ping)
    response = submit(env, transaction_body, "recover-masked-ping")
    assert response.status_code == 202, response.text
    assert failures == [True]
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1
        assert s.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.parametrize("disconnect", [False, True])
def test_storage_failure_rolls_back_and_same_request_can_recover(env, transaction_body, disconnect):
    """A failed write must never acknowledge success or leave a partial job."""
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    engine = env["app"].state.engine

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO atm_test.transactions"):
            if disconnect:
                # Kill only this fixture's own isolated-test connection, never another PID.
                conn.exec_driver_sql("SELECT pg_terminate_backend(pg_backend_pid())")
            raise OperationalError(statement, parameters, RuntimeError("PRIVATE_SENTINEL"))

    event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        response = submit(env, transaction_body, "recover-storage")
    finally:
        event.remove(engine, "before_cursor_execute", fail_insert)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "PRIVATE_SENTINEL" not in response.text
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 0
        assert s.scalar(select(func.count()).select_from(Job)) == 0
    recovered = submit(env, transaction_body, "recover-storage")
    assert recovered.status_code == 202
    replay = submit(env, transaction_body, "recover-storage")
    assert replay.status_code == 202
    assert recovered.json() == replay.json()
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1
        assert s.scalar(select(func.count()).select_from(Case)) == 1


def test_crash_rolls_back_result_and_acknowledgement(env, transaction_body):
    submit(env, transaction_body)
    with pytest.raises(RuntimeError), env["runtime"].begin() as s:
        job = s.scalar(select(Job).with_for_update())
        process(s, job, SimulationScorer())
        job.state = "DONE"
        s.flush()
        raise RuntimeError("simulated process death before commit")
    with env["factory"]() as s:
        assert s.scalar(select(Job)).state == "PENDING"
        assert s.scalar(select(Transaction)).status == "PENDING"
        assert s.scalar(select(func.count()).select_from(Case)) == 0
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Case)) == 1


def test_parallel_workers_claim_distinct_jobs(env, transaction_body):
    for _ in range(12):
        body = {**transaction_body, "external_id": str(uuid4())}
        assert submit(env, body, body["external_id"]).status_code == 202
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_one(env["runtime"], SimulationScorer()), range(12)))
    assert all(results)
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Case)) == 12
        assert s.scalar(select(func.count()).select_from(Job).where(Job.state == "DONE")) == 12


def test_approval_fails_closed_when_audit_cannot_persist(env, transaction_body, monkeypatch):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": 1,
            "action": "FREEZE_ACCOUNT",
            "reason": "Simulated suspicious activity",
        },
    ).json()

    def failed_audit(*args, **kwargs):
        raise Problem(503, "audit_unavailable")

    monkeypatch.setattr("atm_backend.services.audit", failed_audit)
    response = post(
        env,
        f"action-proposals/{proposal['id']}/approval",
        {"expected_version": 1, "approved": True},
        "supervisor",
    )
    assert response.status_code == 503
    with env["factory"]() as s:
        assert s.scalar(select(Proposal)).state == "PENDING"
        assert not s.scalar(select(Job).where(Job.kind == "execute"))


def test_crash_after_mock_action_cannot_duplicate_execution(env, transaction_body):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": 1,
            "action": "NO_ACTION",
            "reason": "Reviewed evidence for simulation",
        },
    ).json()
    post(
        env,
        f"action-proposals/{proposal['id']}/approval",
        {"expected_version": 1, "approved": True},
        "supervisor",
    )
    with pytest.raises(RuntimeError), env["runtime"].begin() as s:
        job = s.scalar(select(Job).where(Job.kind == "execute").with_for_update())
        process(s, job, SimulationScorer())
        s.flush()
        raise RuntimeError("crash before commit")
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"].begin() as s:
        job = s.scalar(select(Job).where(Job.kind == "execute"))
        job.state = "PENDING"  # Deliberately redeliver the same durable message.
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Execution)) == 1


def test_failed_batch_rolls_back_all_items(env, transaction_body):
    existing = {**transaction_body, "idempotency_key": "existing"}
    assert submit(env, transaction_body, "existing").status_code == 202
    new = {**transaction_body, "external_id": "new", "idempotency_key": "new"}
    conflict = {**existing, "amount_minor": 500}
    response = post(env, "transactions/batch", {"items": [new, conflict]}, "ingestion")
    assert response.status_code == 409
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1


def test_rate_limit_is_durable_across_application_instances(env, monkeypatch):
    import time
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from atm_backend.main import create_app

    # Real cloud round trips can cross a minute boundary. Pin only the limiter's
    # clock, keeping JWT verification and request duration measurements real.
    fixed_time = time.time()
    monkeypatch.setattr(
        "atm_backend.main.time", SimpleNamespace(time=lambda: fixed_time, monotonic=time.monotonic)
    )
    env["settings"].requests_per_minute = 10
    for _ in range(10):
        assert env["client"].get("/api/v1/me", headers=env["headers"]()).status_code == 200
    with TestClient(create_app(env["settings"])) as second_instance:
        response = second_instance.get("/api/v1/me", headers=env["headers"]())
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
