from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from atm_backend.adapters import DisabledScorer, SimulationScorer
from atm_backend.main import create_app
from atm_backend.models import Audit, Call, Case, Execution, Job, Principal, Transaction, now
from atm_backend.worker import expire_waits, run_one


def submit(env, body, key="retry-key"):
    return env["client"].post(
        "/api/v1/transactions",
        json=body,
        headers={**env["headers"]("ingestion"), "Idempotency-Key": key},
    )


def case_for(env, body):
    response = submit(env, body, body["external_id"])
    assert response.status_code == 202, response.text
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        return str(
            s.scalar(select(Case.id).where(Case.transaction_id == UUID(response.json()["id"])))
        )


def get_case(env, cid):
    return env["client"].get(f"/api/v1/cases/{cid}", headers=env["headers"]()).json()


def post(env, path, body, who="analyst"):
    return env["client"].post("/api/v1/" + path, json=body, headers=env["headers"](who))


def test_idempotency_atomicity_and_concurrent_duplicates(env, transaction_body):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: submit(env, transaction_body), range(4)))
    assert [r.status_code for r in responses] == [202] * 4
    assert len({r.json()["id"] for r in responses}) == 1
    changed = {**transaction_body, "amount_minor": 12346}
    assert submit(env, changed).status_code == 409
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Transaction)) == 1
        assert s.scalar(select(func.count()).select_from(Job)) == 1
        assert s.scalar(select(func.count()).select_from(Audit)) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_minor", True),
        ("amount_minor", 1.2),
        ("amount_minor", -1),
        ("amount_minor", "100"),
        ("is_fraud", True),
        ("score", 0.9),
        ("currency", "INVALID"),
        ("occurred_at", "2026-01-01T10:00:00"),
    ],
)
def test_ingestion_rejects_bad_values(env, transaction_body, field, value):
    transaction_body[field] = value
    assert submit(env, transaction_body).status_code == 422


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "other"},
        {"iss": "https://evil.example"},
        {"exp": 1},
        {"amr": []},
        {"iat": 1},
        {"auth_time": True},
    ],
)
def test_authentication_claims(env, claims):
    response = env["client"].get("/api/v1/me", headers=env["headers"](**claims))
    assert response.status_code in (401, 403)


def test_revocation_and_roles_are_database_authoritative(env, transaction_body):
    headers = env["headers"]("analyst", role="admin")
    assert env["client"].get("/api/v1/admin/jobs", headers=headers).status_code == 403
    with env["factory"].begin() as s:
        s.get(Principal, env["ids"]["analyst"]).active = False
    assert env["client"].get("/api/v1/me", headers=headers).status_code == 401
    assert env["client"].get("/api/v1/me").status_code == 401


def test_assignment_and_cross_case_denial(env, transaction_body):
    cid = case_for(env, transaction_body)
    response = env["client"].put(
        f"/api/v1/cases/{cid}/assignment",
        json={"expected_version": 1, "analyst_id": str(env["ids"]["analyst"])},
        headers=env["headers"]("supervisor"),
    )
    assert response.status_code == 200, response.text
    for who in ("outsider", "analyst2"):
        for suffix in ("", "/evidence", "/events", "/notes", "/action-proposals"):
            assert (
                env["client"]
                .get(f"/api/v1/cases/{cid}{suffix}", headers=env["headers"](who))
                .status_code
                == 404
            )
    for who in ("auditor", "admin", "ingestion", "participant"):
        assert (
            post(
                env, f"cases/{cid}/notes", {"expected_version": 2, "text": "review note"}, who
            ).status_code
            == 403
        )


@pytest.mark.parametrize("language", ["en", "ar"])
def test_complete_call_approval_decision_scenario(env, transaction_body, language):
    cid = case_for(env, transaction_body)
    assert (
        post(
            env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}
        ).status_code
        == 200
    )
    call = post(
        env,
        f"cases/{cid}/calls",
        {
            "expected_version": 2,
            "participant_id": str(env["ids"]["participant"]),
            "language": language,
        },
    )
    assert call.status_code == 201, call.text
    call_id = call.json()["id"]
    assert (
        env["client"]
        .get(f"/api/v1/calls/{call_id}", headers=env["headers"]("participant2"))
        .status_code
        == 404
    )
    assert (
        post(
            env,
            f"calls/{call_id}/transition",
            {"expected_version": 1, "operation": "answer"},
            "participant",
        ).status_code
        == 200
    )
    assert (
        post(
            env,
            f"calls/{call_id}/turns",
            {
                "expected_version": 2,
                "text": "لم أقم بهذه العملية" if language == "ar" else "I did not authorize it",
            },
            "participant",
        ).status_code
        == 201
    )
    assert (
        post(
            env,
            f"calls/{call_id}/turns",
            {"expected_version": 2, "text": "replayed answer"},
            "participant",
        ).status_code
        == 409
    )
    assert (
        post(
            env,
            f"cases/{cid}/action-proposals",
            {
                "expected_version": 5,
                "action": "HOLD_TRANSFER",
                "reason": "Customer denied transfer",
            },
        ).status_code
        == 409
    )
    assert (
        post(
            env,
            f"calls/{call_id}/transition",
            {"expected_version": 3, "operation": "end"},
            "participant",
        ).status_code
        == 200
    )
    assert (
        post(
            env,
            f"calls/{call_id}/turns",
            {"expected_version": 4, "text": "after hangup"},
            "participant",
        ).status_code
        == 409
    )
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": get_case(env, cid)["version"],
            "action": "HOLD_TRANSFER",
            "reason": "Customer denied transfer",
        },
    )
    assert proposal.status_code == 201, proposal.text
    pid = proposal.json()["id"]
    approval = {"expected_version": 1, "approved": True}
    assert post(env, f"action-proposals/{pid}/approval", approval).status_code == 403
    assert post(env, f"action-proposals/{pid}/approval", approval, "supervisor").status_code == 200
    assert post(env, f"action-proposals/{pid}/approval", approval, "supervisor").status_code == 409
    assert run_one(env["runtime"], SimulationScorer())
    while run_one(env["runtime"], SimulationScorer()):
        pass
    assert (
        post(
            env,
            f"cases/{cid}/decisions",
            {
                "expected_version": get_case(env, cid)["version"],
                "disposition": "CONFIRMED_FRAUD",
                "reason": "Reviewed synthetic evidence",
            },
        ).status_code
        == 201
    )
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Execution)) == 1
    # Recreate the whole application; state is not held in one process/global session.
    from fastapi.testclient import TestClient

    with TestClient(create_app(env["settings"])) as restarted:
        assert (
            restarted.get(f"/api/v1/cases/{cid}", headers=env["headers"]()).json()["state"]
            == "RESOLVED"
        )


@pytest.mark.parametrize("value", ["false", "no", 1, 0, None, [], {}])
def test_approval_never_coerces_truthiness(env, transaction_body, value):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {"expected_version": 1, "action": "NO_ACTION", "reason": "Test approval strictness"},
    ).json()
    assert (
        post(
            env,
            f"action-proposals/{proposal['id']}/approval",
            {"expected_version": 1, "approved": value},
            "supervisor",
        ).status_code
        == 422
    )


def test_evidence_changes_invalidate_approval(env, transaction_body):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {"expected_version": 1, "action": "NO_ACTION", "reason": "Proposed before new evidence"},
    ).json()
    assert (
        post(
            env,
            f"cases/{cid}/notes",
            {"expected_version": 1, "text": "Additional evidence arrived"},
        ).status_code
        == 201
    )
    assert (
        post(
            env,
            f"action-proposals/{proposal['id']}/approval",
            {"expected_version": 1, "approved": True},
            "supervisor",
        ).json()["error"]["code"]
        == "evidence_changed"
    )


def test_independent_and_fresh_approval(env, transaction_body):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": 1,
            "action": "FREEZE_ACCOUNT",
            "reason": "Synthetic high impact action",
        },
        "supervisor",
    ).json()
    assert (
        post(
            env,
            f"action-proposals/{proposal['id']}/approval",
            {"expected_version": 1, "approved": True},
            "supervisor",
        ).json()["error"]["code"]
        == "independent_approval_required"
    )
    response = env["client"].post(
        f"/api/v1/action-proposals/{proposal['id']}/approval",
        json={"expected_version": 1, "approved": False},
        headers=env["headers"]("supervisor", auth_time=int(now().timestamp()) - 400),
    )
    assert response.json()["error"]["code"] == "fresh_authentication_required"


def test_future_history_excluded(env, transaction_body):
    cid = case_for(env, transaction_body)
    future = {
        **transaction_body,
        "external_id": str(uuid4()),
        "occurred_at": "2026-02-01T10:00:00Z",
    }
    submit(env, future)
    result = env["client"].get(f"/api/v1/cases/{cid}/evidence", headers=env["headers"]()).json()
    assert len(result["history"]) == 1
    assert result["history"][0]["external_id"] == transaction_body["external_id"]


def test_worker_failure_is_not_normal_and_recovers(env, transaction_body):
    submit(env, transaction_body)
    for _ in range(5):
        assert run_one(env["runtime"], DisabledScorer())
        with env["factory"].begin() as s:
            job = s.scalar(select(Job))
            job.available_at = now() - timedelta(seconds=1)
    with env["factory"]() as s:
        row = s.scalar(select(Transaction))
        assert row.status == "FAILED" and row.result is None
        assert s.scalar(select(Job)).state == "FAILED"
        assert s.scalar(select(Case)).transaction_id == row.id


def test_audit_is_protected_at_database_layer(env, transaction_body):
    submit(env, transaction_body)
    for statement in (
        "DELETE FROM atm_test.audit_events",
        "UPDATE atm_test.audit_events SET kind='changed'",
        "UPDATE atm_test.principals SET role='supervisor'",
        "DROP TABLE atm_test.audit_events",
    ):
        with pytest.raises(DBAPIError), env["runtime"].begin() as s:
            s.execute(text(statement))


def test_timeout_releases_case(env, transaction_body):
    cid = case_for(env, transaction_body)
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    response = post(
        env,
        f"cases/{cid}/calls",
        {"expected_version": 2, "participant_id": str(env["ids"]["participant"]), "language": "ar"},
    )
    with env["factory"].begin() as s:
        s.get(Call, UUID(response.json()["id"])).expires_at = now() - timedelta(seconds=1)
    expire_waits(env["runtime"])
    assert get_case(env, cid)["state"] == "IN_REVIEW"
    assert (
        env["client"]
        .get(f"/api/v1/calls/{response.json()['id']}", headers=env["headers"]("participant"))
        .json()["state"]
        == "TIMED_OUT"
    )


def test_body_limit_and_error_redaction(env):
    result = env["client"].post("/api/v1/transactions", content=b"x" * 140000)
    assert result.status_code == 413
    assert result.headers["cache-control"] == "no-store"
    result = env["client"].post(
        "/api/v1/transactions",
        json={"secret": "SENSITIVE_SENTINEL"},
        headers={**env["headers"]("ingestion"), "Idempotency-Key": "one"},
    )
    assert result.status_code == 422 and "SENSITIVE_SENTINEL" not in result.text
