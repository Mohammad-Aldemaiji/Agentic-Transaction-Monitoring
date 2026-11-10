from datetime import timedelta
from unittest.mock import patch

from test_workflows import case_for, post

from atm_backend.adapters import SimulationScorer
from atm_backend.models import now
from atm_backend.services import GUIDED_STEPS
from atm_backend.worker import expire_waits, run_one


def prepare(env, body):
    cid = case_for(env, body)
    assert (
        post(
            env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}
        ).status_code
        == 200
    )
    for step in GUIDED_STEPS:
        outcome = (
            "MANUAL_REVIEW"
            if step == "assessment"
            else "NOT_REQUIRED"
            if step == "verification"
            else "REVIEWED"
        )
        result = post(
            env,
            f"cases/{cid}/investigation-progress",
            {
                "expected_version": 2,
                "step": step,
                "outcome": outcome,
                "reason": "Reviewed available synthetic evidence manually",
            },
        )
        assert result.status_code == 200, result.text
    return cid


def proposal(env, cid, who="analyst"):
    return post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": 2,
            "action": "NO_ACTION",
            "reason": "Reviewed evidence supports no further action",
        },
        who,
    )


def test_guided_progress_persists_and_requires_order_and_evidence(env, transaction_body):
    cid = case_for(env, transaction_body)
    assert proposal(env, cid).status_code == 409
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    payload = {
        "expected_version": 2,
        "step": "assessment",
        "outcome": "REVIEWED",
        "reason": "Evidence reviewed carefully",
    }
    assert post(env, f"cases/{cid}/investigation-progress", payload).status_code == 409
    for step in ("overview", "scoring"):
        assert (
            post(env, f"cases/{cid}/investigation-progress", {**payload, "step": step}).status_code
            == 200
        )
    assert (
        post(env, f"cases/{cid}/investigation-progress", payload).json()["error"]["code"]
        == "current_assessment_required"
    )
    assert (
        post(
            env, f"cases/{cid}/investigation-progress", {**payload, "outcome": "MANUAL_REVIEW"}
        ).status_code
        == 200
    )
    assert (
        post(
            env, f"cases/{cid}/investigation-progress", {**payload, "step": "verification"}
        ).json()["error"]["code"]
        == "verification_outcome_required"
    )
    assert proposal(env, cid).status_code == 201
    read = env["client"].get(
        f"/api/v1/cases/{cid}/investigation-progress", headers=env["headers"]()
    )
    assert set(read.json()) == {"overview", "scoring", "assessment"}
    assert (
        env["client"]
        .get(f"/api/v1/cases/{cid}/investigation-progress", headers=env["headers"]("outsider"))
        .status_code
        == 404
    )


def test_tabbed_review_can_propose_without_legacy_step_records(env, transaction_body):
    cid = case_for(env, transaction_body)
    result = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": 1,
            "action": "NO_ACTION",
            "reason": "Reviewed the payment and customer history",
        },
    )
    assert result.json()["error"]["code"] == "investigation_required"
    assert (
        post(
            env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}
        ).status_code
        == 200
    )
    progress = env["client"].get(
        f"/api/v1/cases/{cid}/investigation-progress", headers=env["headers"]()
    )
    assert progress.json() == {}
    result = proposal(env, cid)
    assert result.status_code == 201, result.text
    assert result.json()["state"] == "PENDING"
    assert result.json()["expires_at"] is None
    assert proposal(env, cid).json()["error"]["code"] == "pending_proposals"
    assert (
        post(
            env,
            f"action-proposals/{result.json()['id']}/approval",
            {"expected_version": 1, "approved": True},
        ).status_code
        == 403
    )


def test_pending_survives_days_and_only_supervisor_can_decide(env, transaction_body):
    cid = prepare(env, transaction_body)
    result = proposal(env, cid)
    assert result.status_code == 201, result.text
    p = result.json()
    assert p["expires_at"] is None
    assert proposal(env, cid).status_code == 409
    with patch("atm_backend.worker.now", return_value=now() + timedelta(days=30)):
        expire_waits(env["runtime"])
    assert (
        env["client"]
        .get(f"/api/v1/cases/{cid}/summary", headers=env["headers"]())
        .json()["pending_proposals"]
        == 1
    )
    decision = {
        "expected_version": 2,
        "disposition": "LEGITIMATE",
        "reason": "Independent evidence review completed",
    }
    assert post(env, f"cases/{cid}/decisions", decision).status_code == 403
    assert post(env, f"cases/{cid}/decisions", decision, "supervisor").status_code == 409
    approval = {"expected_version": 1, "approved": True}
    assert post(env, f"action-proposals/{p['id']}/approval", approval).status_code == 403
    assert (
        post(env, f"action-proposals/{p['id']}/approval", approval, "supervisor").status_code == 200
    )
    assert post(env, f"cases/{cid}/decisions", decision, "supervisor").status_code == 409
    assert run_one(env["runtime"], SimulationScorer())
    assert post(env, f"cases/{cid}/decisions", decision, "supervisor").status_code == 201
    assert post(env, f"cases/{cid}/decisions", decision, "supervisor").status_code == 409


def test_changed_evidence_blocks_approval_and_return_allows_resubmission(env, transaction_body):
    cid = prepare(env, transaction_body)
    p = proposal(env, cid).json()
    assert (
        post(
            env,
            f"cases/{cid}/notes",
            {"expected_version": 2, "text": "Additional evidence changed the investigation"},
        ).status_code
        == 201
    )
    approval = post(
        env,
        f"action-proposals/{p['id']}/approval",
        {"expected_version": 1, "approved": True},
        "supervisor",
    )
    assert approval.json()["error"]["code"] == "evidence_changed"
    assert (
        post(
            env,
            f"action-proposals/{p['id']}/cancel",
            {"expected_version": 1, "reason": "Please revise with the latest evidence"},
            "supervisor",
        ).status_code
        == 200
    )
    assert (
        post(
            env,
            f"cases/{cid}/action-proposals",
            {
                "expected_version": 3,
                "action": "NO_ACTION",
                "reason": "Updated evidence reviewed before resubmission",
            },
        ).status_code
        == 201
    )


def test_supervisor_cannot_review_own_proposal(env, transaction_body):
    cid = prepare(env, transaction_body)
    p = proposal(env, cid, "supervisor").json()
    for approved in (True, False):
        result = post(
            env,
            f"action-proposals/{p['id']}/approval",
            {"expected_version": 1, "approved": approved},
            "supervisor",
        )
        assert result.status_code == 403
