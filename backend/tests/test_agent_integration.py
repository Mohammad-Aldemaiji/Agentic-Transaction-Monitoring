"""Real database checks for permissions, stale evidence, and model approval."""

import base64
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import test_local_ai as local_ai_fixtures
from sqlalchemy import func, select
from test_local_ai import analysis
from test_workflows import case_for, post, submit

from atm_backend.adapters import ScoringUnavailable
from atm_backend.e05 import E05Scorer, replay_payload
from atm_backend.local_agent import AgentUnavailable, Analysis
from atm_backend.models import (
    CallTurn,
    Case,
    Event,
    Execution,
    Job,
    ModelVersion,
    Proposal,
    Transaction,
    now,
)
from atm_backend.worker import process, run_one

e05_settings = local_ai_fixtures.e05_settings
sample = local_ai_fixtures.sample


def start(env, body):
    cid = case_for(env, body)
    assert (
        post(
            env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}
        ).status_code
        == 200
    )
    env["settings"].agent_enabled = True
    return cid


def test_agent_scoped_advisory_and_stale_evidence(env, transaction_body, monkeypatch):
    cid = start(env, transaction_body)
    path = f"cases/{cid}/agent-analysis"
    for who, status in [("outsider", 404), ("participant", 403), ("auditor", 403)]:
        assert post(env, path, {"expected_version": 2}, who).status_code == status
    with env["factory"].begin() as s:
        original = s.scalar(
            select(Transaction).where(Transaction.external_id == transaction_body["external_id"])
        )
        for team, account, timestamp in [
            ("beta", original.account_id, original.occurred_at),
            ("alpha", "other-account", original.occurred_at),
            ("alpha", original.account_id, original.occurred_at + timedelta(hours=1)),
        ]:
            s.add(
                Transaction(
                    team=team,
                    external_id=str(uuid4()),
                    account_id=account,
                    idempotency_key=str(uuid4()),
                    payload_hash="a" * 64,
                    amount_minor=100,
                    currency="SAR",
                    channel="TRANSFER",
                    occurred_at=timestamp,
                    received_at=now() - timedelta(days=1),
                    context={},
                )
            )
    captured = []

    def analyse(evidence):
        captured.append(evidence)
        assert len(evidence["transactions"]) == 1
        return Analysis.model_validate(analysis([evidence["alert_transaction_id"]]))

    monkeypatch.setattr(env["app"].state.local_agent, "analyse", analyse)
    result = post(env, path, {"expected_version": 2})
    assert result.status_code == 200, result.text
    assert result.json()["details"]["advisory_only"] is True
    with env["factory"]() as s:
        assert s.scalar(select(func.count()).select_from(Proposal)) == 0
        assert s.scalar(select(func.count()).select_from(Execution)) == 0
        assert s.get(Case, UUID(cid)).version == 3
    assert post(env, path, {"expected_version": 2}).status_code == 409

    def race(evidence):
        # Must succeed while inference runs: no database lock is held by the AI request.
        assert (
            post(
                env, f"cases/{cid}/notes", {"expected_version": 3, "text": "New evidence arrived"}
            ).status_code
            == 201
        )
        return analyse(evidence)

    monkeypatch.setattr(env["app"].state.local_agent, "analyse", race)
    assert post(env, path, {"expected_version": 3}).status_code == 409
    with env["factory"]() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(Event).where(Event.kind == "investigation.finding")
            )
            == 1
        )


def test_agent_failure_leaves_case_reviewable(env, transaction_body, monkeypatch):
    cid = start(env, transaction_body)

    def unavailable(_):
        raise AgentUnavailable("Sensitive provider details must not be exposed")

    monkeypatch.setattr(env["app"].state.local_agent, "analyse", unavailable)
    response = post(env, f"cases/{cid}/agent-analysis", {"expected_version": 2})
    assert response.status_code == 503
    assert "Sensitive" not in response.text
    with env["factory"]() as s:
        assert s.get(Case, UUID(cid)).version == 2
        assert s.scalar(select(func.count()).select_from(Execution)) == 0


def test_real_e05_requires_approved_model_before_scoring(env, e05_settings, sample):
    response = submit(env, replay_payload(sample), "e05-approval-test")
    assert response.status_code == 202
    scorer = E05Scorer(e05_settings)
    with env["runtime"].begin() as s:
        job = s.scalar(select(Job).where(Job.object_id == UUID(response.json()["id"])))
        with pytest.raises(ScoringUnavailable):
            process(s, job, scorer)
    with env["factory"].begin() as s:
        s.add(
            ModelVersion(
                team="alpha",
                name="fraud_e05",
                digest=e05_settings.scoring_model_digest,
                simulation=False,
                state="APPROVED",
                metrics={},
                registered_by=env["ids"]["admin"],
            )
        )
    assert run_one(env["runtime"], scorer)
    with env["factory"]() as s:
        transaction = s.get(Transaction, UUID(response.json()["id"]))
        assert transaction.status == "SCORED"
        assert transaction.result["risk_score"] == transaction.result["supervised_probability"]
        assert transaction.result["anomaly_score"] is None
        assert s.scalar(select(Case).where(Case.transaction_id == transaction.id)) is not None


def test_transcription_is_owned_versioned_draft_only(env, transaction_body, monkeypatch, tmp_path):
    cid = start(env, transaction_body)
    response = post(
        env,
        f"cases/{cid}/calls",
        {"expected_version": 2, "participant_id": str(env["ids"]["participant"]), "language": "en"},
    )
    assert response.status_code == 201, response.text
    call_id = response.json()["id"]
    assert (
        post(
            env,
            f"calls/{call_id}/transition",
            {"expected_version": 1, "operation": "answer"},
            "participant",
        ).status_code
        == 200
    )
    env["settings"].whisper_model_dir = tmp_path
    monkeypatch.setattr(
        "atm_backend.workflows.run_voice", lambda *args: b"I did not make this payment"
    )
    payload = {"expected_version": 2, "audio_base64": base64.b64encode(b"test audio").decode()}
    path = f"calls/{call_id}/transcribe"
    assert post(env, path, payload, "participant2").status_code == 404
    assert post(env, path, payload, "analyst").status_code == 403
    assert post(env, path, payload | {"expected_version": 1}, "participant").status_code == 409
    result = post(env, path, payload, "participant")
    assert result.status_code == 200, result.text
    assert result.json() == {"text": "I did not make this payment", "draft_only": True}
    with env["factory"]() as s:
        assert (
            s.scalar(
                select(func.count()).select_from(CallTurn).where(CallTurn.speaker == "customer")
            )
            == 0
        )
        assert s.scalar(select(func.count()).select_from(Execution)) == 0
