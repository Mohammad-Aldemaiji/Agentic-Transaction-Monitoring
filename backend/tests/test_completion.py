from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from test_workflows import case_for, post, submit

from atm_backend.adapters import Score, ScoringUnavailable
from atm_backend.models import Audit, Case, PolicyVersion, Proposal, RateBucket, Transaction, now
from atm_backend.worker import expire_waits, run_one


def test_audit_export_terminates_while_its_reads_are_audited(env, transaction_body):
    case_for(env, transaction_body)
    client, headers = env["client"], env["headers"]("auditor")
    first = client.get("/api/v1/audit/events?limit=1", headers=headers).json()
    watermark, cursor = first["through"], first["next_cursor"]
    exported = first["items"]
    for _ in range(20):
        page = client.get(
            f"/api/v1/audit/events?limit=1&after={cursor}&through={watermark}", headers=headers
        ).json()
        exported += page["items"]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    else:
        pytest.fail("Audit export did not terminate")
    assert all(row["id"] <= watermark for row in exported)
    assert len({row["id"] for row in exported}) == len(exported)
    assert client.get("/api/v1/audit/events", headers=env["headers"]()).status_code == 403


def test_unknown_session_cannot_fill_rate_limit_storage(env):
    env["client"].cookies.set("atm_session", "attacker-chosen-random-cookie")
    assert env["client"].get("/api/v1/me").status_code == 401
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(RateBucket)) == 0


def test_runtime_connection_enforces_database_timeouts(env):
    with env["runtime"]() as s:
        assert s.scalar(text("SHOW statement_timeout")) == "10s"
        assert s.scalar(text("SHOW lock_timeout")) == "3s"


@pytest.mark.parametrize(
    "threshold,provider_review,expected",
    [(0.7, False, False), (0.4, False, True), (0.7, True, True)],
)
def test_scoring_records_and_applies_approved_team_policy(
    env, transaction_body, threshold, provider_review, expected
):
    with env["factory"].begin() as s:
        s.add(
            PolicyVersion(
                team="alpha",
                version=1,
                threshold=threshold,
                state="ACTIVE",
                created_by=env["ids"]["admin"],
                approved_by=env["ids"]["supervisor"],
                reason="Test approved policy",
            )
        )
    response = submit(env, transaction_body)

    class Provider:
        def score(self, transaction, features):
            return Score(False, "test-provider", 0.5, provider_review, ["TEST_ONLY"])

    assert run_one(env["runtime"], Provider())
    with env["factory"]() as s:
        txn = s.get(Transaction, UUID(response.json()["id"]))
        assert txn.result["policy_version"] == 1
        assert txn.result["requires_review"] is expected
        assert (s.scalar(select(Case)) is not None) is expected


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01, True])
def test_invalid_provider_scores_fail_closed(score):
    with pytest.raises(ScoringUnavailable):
        Score(False, "invalid-provider", score, False, ["TEST_ONLY"])


def test_expired_proposals_are_audited_once(env, transaction_body, monkeypatch):
    cid = case_for(env, transaction_body)
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {"expected_version": 1, "action": "HOLD_TRANSFER", "reason": "Synthetic review"},
    ).json()
    future = now() + timedelta(minutes=11)
    monkeypatch.setattr("atm_backend.worker.now", lambda: future)
    expire_waits(env["runtime"])
    expire_waits(env["runtime"])
    with env["factory"]() as s:
        assert s.get(Proposal, UUID(proposal["id"])).state == "EXPIRED"
        assert len(list(s.scalars(select(Audit).where(Audit.kind == "action.expired")))) == 1


def test_bank_and_customer_turns_share_versioned_call(env, transaction_body):
    cid = case_for(env, transaction_body)
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    call = post(
        env,
        f"cases/{cid}/calls",
        {"expected_version": 2, "participant_id": str(env["ids"]["participant"]), "language": "ar"},
    ).json()
    path = f"calls/{call['id']}"
    assert (
        post(
            env, path + "/transition", {"expected_version": 1, "operation": "answer"}, "participant"
        ).status_code
        == 200
    )
    bank = post(env, path + "/turns", {"expected_version": 2, "text": "هل تؤكد هذه العملية؟"})
    assert bank.status_code == 201 and bank.json()["speaker"] == "bank"
    assert (
        post(
            env, path + "/turns", {"expected_version": 3, "text": "نعم"}, "participant"
        ).status_code
        == 201
    )
    assert (
        post(
            env, path + "/turns", {"expected_version": 4, "text": "intrusion"}, "outsider"
        ).status_code
        == 404
    )


def test_late_arriving_history_cannot_change_case_evidence(env, transaction_body):
    cid = case_for(env, transaction_body)
    late = {**transaction_body, "external_id": str(uuid4()), "occurred_at": "2025-12-31T10:00:00Z"}
    assert submit(env, late, "late-history").status_code == 202
    response = env["client"].get(f"/api/v1/cases/{cid}/evidence", headers=env["headers"]())
    assert response.status_code == 200
    assert late["external_id"] not in response.text
