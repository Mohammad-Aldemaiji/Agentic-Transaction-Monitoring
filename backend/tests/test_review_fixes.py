"""Regression checks for customer evidence, latest findings and policy-aware counts."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from test_agent_integration import start
from test_local_ai import analysis
from test_review_capacity import propose
from test_workflows import case_for, post

from atm_backend.booth import register
from atm_backend.local_agent import Analysis
from atm_backend.models import Call, CallTurn, Case, Event, Investigation, Job, now


def test_ai_receives_bounded_case_scoped_calls_and_latest_finding(
    env, transaction_body, monkeypatch
):
    cid = start(env, transaction_body)
    other = start(env, {**transaction_body, "external_id": "another-case"})
    with env["factory"].begin() as s:
        inv = s.scalar(select(Investigation).where(Investigation.case_id == UUID(cid)))
        other_inv = s.scalar(select(Investigation).where(Investigation.case_id == UUID(other)))
        calls = []
        for i in range(4):
            call = Call(
                investigation_id=inv.id,
                participant_id=env["ids"]["participant"],
                state="ENDED",
                language="en",
                expires_at=now(),
                created_at=now() - timedelta(minutes=i),
            )
            s.add(call)
            s.flush()
            calls.append(call.id)
            for sequence in range(9):
                s.add(
                    CallTurn(
                        call_id=call.id,
                        sequence=sequence,
                        speaker="customer",
                        text="No, I did not authorize this payment."
                        if sequence == 8
                        else "Earlier context " * 100,
                    )
                )
        foreign = Call(
            investigation_id=other_inv.id,
            participant_id=env["ids"]["participant"],
            state="DECLINED",
            language="en",
            expires_at=now(),
        )
        s.add(foreign)
        s.flush()
        foreign_id = str(foreign.id)
        s.add(Job(team="alpha", kind="voice", object_id=calls[0], state="FAILED"))
        s.add_all(
            [
                Event(case_id=UUID(cid), kind="investigation.finding", details={"source": "manual"})
                for _ in range(55)
            ]
        )

    def analyse(evidence):
        history = evidence["customer_verification"]
        assert len(history) == 3 and evidence["verification_history_truncated"]
        assert foreign_id not in {c["call_id"] for c in history}
        assert history[0]["transcript"][-1]["text"] == "No, I did not authorize this payment."
        assert any(c["transcript_truncated"] for c in history)
        assert sum(len(t["text"]) for c in history for t in c["transcript"]) <= 1000
        return Analysis.model_validate(analysis([evidence["alert_transaction_id"]]))

    monkeypatch.setattr(env["app"].state.local_agent, "analyse", analyse)
    first = post(env, f"cases/{cid}/agent-analysis", {"expected_version": 2})
    assert first.status_code == 200, first.text
    latest = post(env, f"cases/{cid}/agent-analysis", {"expected_version": 3})
    assert latest.status_code == 200, latest.text
    path = f"/api/v1/cases/{cid}/findings?latest_ai=true"
    result = env["client"].get(path, headers=env["headers"]()).json()
    assert [r["id"] for r in result["items"]] == [latest.json()["id"]]
    assert result["next_cursor"] is None
    assert env["client"].get(path, headers=env["headers"]("outsider")).status_code == 404
    audio = env["client"].get(f"/api/v1/calls/{calls[0]}/audio", headers=env["headers"]()).json()
    assert audio["status"] == "FAILED" and audio["text_available"]
    assert (
        env["client"]
        .get(f"/api/v1/calls/{calls[0]}/audio", headers=env["headers"]("participant2"))
        .status_code
        == 404
    )


def test_payment_counts_follow_recorded_policy_bands(env, transaction_body, monkeypatch):
    policy = propose(env).json()  # Critical above .3, not the display default of .85.
    assert (
        post(env, f"admin/risk-policy/{policy['id']}/activate", {}, "supervisor").status_code == 200
    )
    cid = case_for(env, transaction_body)  # Simulation scorer returns .5.
    with env["factory"]() as s:
        assert s.get(Case, UUID(cid)) is not None
    # Install presentation routes only in this already-isolated test app.
    monkeypatch.setattr(env["settings"], "environment", "booth")
    register(env["app"])
    response = env["client"].get("/api/v1/booth/payments", headers=env["headers"]())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["items"][0]["result"]["severity"] == "CRITICAL"
    assert result["stats"]["critical"] == 1 and result["stats"]["medium"] == 0
