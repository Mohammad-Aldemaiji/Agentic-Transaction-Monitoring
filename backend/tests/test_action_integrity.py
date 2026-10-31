from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_workflows import case_for, post

from atm_backend.adapters import SimulationScorer
from atm_backend.models import Audit, Case, Event, Execution, Job, Proposal
from atm_backend.worker import process, run_one


def proposal_for(env, body, approved=True):
    cid = case_for(env, body)
    response = post(
        env,
        f"cases/{cid}/action-proposals",
        {"expected_version": 1, "action": "HOLD_TRANSFER", "reason": "Synthetic transfer review"},
    )
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    if approved:
        result = post(
            env,
            f"action-proposals/{pid}/approval",
            {"expected_version": 1, "approved": True},
            "supervisor",
        )
        assert result.status_code == 200, result.text
    return cid, pid


def cancellation(env, pid, version=2, who="supervisor"):
    return post(
        env,
        f"action-proposals/{pid}/cancel",
        {"expected_version": version, "reason": "Additional review is required"},
        who,
    )


def test_approved_work_blocks_closure_and_evidence_changes_until_receipt(env, transaction_body):
    cid, _ = proposal_for(env, transaction_body)
    decision = {
        "expected_version": 1,
        "disposition": "CONFIRMED_FRAUD",
        "reason": "Reviewed synthetic case",
    }
    for path, body in [
        (f"cases/{cid}/decisions", decision),
        (f"cases/{cid}/notes", {"expected_version": 1, "text": "Changed evidence"}),
        (f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}),
        (
            f"cases/{cid}/action-proposals",
            {"expected_version": 1, "action": "NO_ACTION", "reason": "Another action"},
        ),
    ]:
        response = post(env, path, body)
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "approved_action_pending"
        )
    assert run_one(env["runtime"], SimulationScorer())
    assert post(env, f"cases/{cid}/decisions", decision).status_code == 201


def test_cancel_requires_scope_supervisor_and_fresh_auth_then_releases_case(env, transaction_body):
    cid, pid = proposal_for(env, transaction_body)
    assert cancellation(env, pid, who="analyst").status_code == 403
    assert cancellation(env, pid, who="outsider").status_code == 404
    from atm_backend.models import now

    stale = env["client"].post(
        f"/api/v1/action-proposals/{pid}/cancel",
        headers=env["headers"]("supervisor", auth_time=int(now().timestamp()) - 601),
        json={"expected_version": 2, "reason": "Additional review required"},
    )
    assert stale.status_code == 403
    assert cancellation(env, pid).status_code == 200
    assert cancellation(env, pid, version=3).status_code == 409
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert not s.scalar(select(Execution))
        assert s.scalar(select(Job).where(Job.kind == "execute")).state == "DONE"
        assert len(list(s.scalars(select(Audit).where(Audit.kind == "action.cancelled")))) == 1
        assert (
            s.scalar(select(Event).where(Event.kind == "action.cancelled")).details["reason"]
            == "Additional review is required"
        )
    assert (
        post(
            env, f"cases/{cid}/notes", {"expected_version": 1, "text": "Review resumed"}
        ).status_code
        == 201
    )


def test_proposer_can_cancel_pending_but_not_another_analysts_proposal(env, transaction_body):
    _, pid = proposal_for(env, transaction_body, approved=False)
    assert cancellation(env, pid, version=1, who="analyst2").status_code == 403
    assert cancellation(env, pid, version=1, who="analyst").status_code == 200
    assert (
        post(
            env,
            f"action-proposals/{pid}/approval",
            {"expected_version": 2, "approved": True},
            "supervisor",
        ).status_code
        == 409
    )


def test_worker_refreshes_cached_approval_after_cancellation(env, transaction_body):
    _, pid = proposal_for(env, transaction_body)
    with env["runtime"].begin() as s:
        cached = s.get(Proposal, UUID(pid))
        assert cached.state == "APPROVED"
        assert cancellation(env, pid).status_code == 200
        job = s.scalar(select(Job).where(Job.kind == "execute").with_for_update())
        process(s, job, SimulationScorer())
        assert cached.state == "CANCELLED"
        assert not s.scalar(select(Execution))


def test_cancellation_racing_execution_has_one_winner(env, transaction_body):
    _, pid = proposal_for(env, transaction_body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(run_one, env["runtime"], SimulationScorer())
        cancel = pool.submit(cancellation, env, pid)
        assert worker.result()
        response = cancel.result()
    with env["factory"]() as s:
        row = s.get(Proposal, UUID(pid))
        receipt = s.scalar(select(Execution))
        if row.state == "CANCELLED":
            assert response.status_code == 200 and receipt is None
        else:
            assert response.status_code == 409 and receipt is not None
    assert (
        cancellation(env, pid, version=3 if response.status_code == 200 else 2).status_code == 409
    )


def test_database_rejects_action_tampering_and_unapproved_receipts(env, transaction_body):
    _, pid = proposal_for(env, transaction_body, approved=False)
    for assignment in (
        "target='other-account'",
        "reason='rewritten reason'",
        "evidence_version=99",
        "version=99",
    ):
        with pytest.raises(DBAPIError), env["runtime"].begin() as s:
            s.execute(
                text(f"UPDATE atm_test.proposals SET {assignment} WHERE id=:id"), {"id": UUID(pid)}
            )
    with pytest.raises(DBAPIError), env["runtime"].begin() as s:
        s.add(Execution(proposal_id=UUID(pid), receipt={"simulation": True, "status": "SUCCEEDED"}))
        s.flush()
    assert cancellation(env, pid, version=1, who="analyst").status_code == 200


def test_worker_refuses_stale_evidence_and_supervisor_can_recover(env, transaction_body):
    cid, pid = proposal_for(env, transaction_body)
    with env["factory"].begin() as s:
        s.get(Case, UUID(cid)).version += 1  # Simulate an out-of-band operator modification.
    assert run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert not s.scalar(select(Execution))
        assert (
            s.scalar(select(Job).where(Job.kind == "execute")).error_code
            == "processing_unavailable"
        )
    assert cancellation(env, pid).status_code == 200


def test_execution_status_distinguishes_waiting_failure_and_cancellation(env, transaction_body):
    _, pid = proposal_for(env, transaction_body, approved=False)
    url = f"/api/v1/action-proposals/{pid}/execution"

    def status():
        response = env["client"].get(url, headers=env["headers"]())
        assert response.status_code == 200
        return response.json()

    assert status()["status"] == "AWAITING_APPROVAL"
    assert (
        post(
            env,
            f"action-proposals/{pid}/approval",
            {"expected_version": 1, "approved": True},
            "supervisor",
        ).status_code
        == 200
    )
    assert status()["status"] == "PENDING"
    with env["factory"].begin() as s:
        job = s.scalar(select(Job).where(Job.kind == "execute"))
        job.state, job.attempts, job.error_code = "FAILED", 5, "processing_unavailable"
    assert status() == {"status": "FAILED", "attempts": 5, "error_code": "processing_unavailable"}
    assert cancellation(env, pid).status_code == 200
    assert status()["status"] == "CANCELLED"
