"""A declined call and rejected proposal must not strand a case or execute an action."""

from sqlalchemy import func, select
from test_workflows import case_for, get_case, post

from atm_backend.adapters import SimulationScorer
from atm_backend.models import Audit, Execution, Job
from atm_backend.worker import run_one


def test_declined_call_and_rejected_action_allow_reviewed_resolution(env, transaction_body):
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
        {"expected_version": 2, "participant_id": str(env["ids"]["participant"]), "language": "en"},
    )
    assert call.status_code == 201
    call_id = call.json()["id"]
    declined = post(
        env,
        f"calls/{call_id}/transition",
        {"expected_version": 1, "operation": "decline"},
        "participant",
    )
    assert declined.status_code == 200 and declined.json()["state"] == "DECLINED"
    assert get_case(env, cid)["state"] == "IN_REVIEW"
    assert (
        post(
            env,
            f"calls/{call_id}/transition",
            {"expected_version": 2, "operation": "answer"},
            "participant",
        ).status_code
        == 409
    )
    assert (
        post(
            env,
            f"calls/{call_id}/turns",
            {"expected_version": 2, "text": "Late answer"},
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
            "reason": "Review potential hold after unsuccessful contact",
        },
    )
    assert proposal.status_code == 201
    pid = proposal.json()["id"]
    rejected = post(
        env,
        f"action-proposals/{pid}/approval",
        {"expected_version": 1, "approved": False},
        "supervisor",
    )
    assert rejected.status_code == 200 and rejected.json()["state"] == "REJECTED"
    assert (
        post(
            env,
            f"action-proposals/{pid}/approval",
            {"expected_version": 2, "approved": True},
            "supervisor",
        ).status_code
        == 409
    )
    assert not run_one(env["runtime"], SimulationScorer())
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Execution)) == 0
        assert (
            s.scalar(
                select(func.count()).select_from(Job).where(Job.kind.in_(["voice", "execute"]))
            )
            == 0
        )
        for action, target in [("call.decline", call_id), ("action.rejected", pid)]:
            assert s.scalar(select(Audit).where(Audit.kind == action, Audit.object_id == target))
    decision = post(
        env,
        f"cases/{cid}/decisions",
        {
            "expected_version": get_case(env, cid)["version"],
            "disposition": "LEGITIMATE",
            "reason": "Reviewed independent synthetic evidence; declined call is inconclusive",
        },
    )
    assert decision.status_code == 201
    assert get_case(env, cid)["state"] == "RESOLVED"
