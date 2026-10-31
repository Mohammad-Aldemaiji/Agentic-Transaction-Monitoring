"""Local model parity, fail-closed inputs, and the advisory agent's trust boundary."""

import asyncio
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_scoring_contract import config

from atm_backend.adapters import ScoringUnavailable
from atm_backend.e05 import E05Scorer, model_digest, replay_payload
from atm_backend.local_agent import AgentUnavailable, Analysis, LocalAgent
from atm_backend.models import Transaction

ROOT = Path(__file__).resolve().parents[2]


def analysis(ids):
    return {
        "disposition": "UNCERTAIN",
        "confidence": 0.5,
        "suspected_pattern": "card testing",
        "evidence_for_fraud": ["Unusual transaction"],
        "evidence_against_fraud": [],
        "recommended_action": "VERIFY_WITH_CUSTOMER",
        "recommendation_reason": "Confirm activity",
        "summary": "The available evidence requires human review.",
        "evidence_transaction_ids": ids,
    }


@pytest.fixture
def e05_settings(signing_keys):
    directory = ROOT / "models/fraud_e05"
    data = ROOT / "data/sample/transactions_mini.csv"
    return config(
        signing_keys,
        scoring_mode="e05_sample",
        scoring_model_name="fraud_e05",
        scoring_model_digest=model_digest(directory),
        e05_model_dir=directory,
        e05_data_file=data,
        e05_data_digest=hashlib.sha256(data.read_bytes()).hexdigest(),
    )


@pytest.fixture
def sample():
    with (ROOT / "data/sample/transactions_mini.csv").open() as stream:
        return next(r for r in csv.DictReader(stream) if r["txn_id"] == "TX000733650")


def as_transaction(sample):
    payload = replay_payload(sample)
    payload["occurred_at"] = datetime.fromisoformat(payload["occurred_at"])
    return Transaction(id=uuid4(), context={}, **payload)


def test_e05_scores_real_model_and_produces_five_explanations(e05_settings, sample):
    result = E05Scorer(e05_settings).score(as_transaction(sample), {})
    assert result.model_version == "fraud_e05" and not result.simulation
    assert result.risk_score == result.supervised_probability and result.requires_review
    assert result.risk_score == pytest.approx(0.5000187754631042)
    assert len(result.contributions) == len(result.reasons) == 5
    assert result.supervised_probability > 0.5
    assert result.anomaly_score is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_minor", 1),
        ("account_id", "another-account"),
        ("currency", "USD"),
        ("channel", "ATM"),
        ("external_id", "missing"),
        ("context", {"device_id": "override"}),
    ],
)
def test_e05_rejects_changed_or_missing_evidence(e05_settings, sample, field, value):
    scorer = E05Scorer(e05_settings)
    txn = as_transaction(sample)
    setattr(txn, field, value)
    with pytest.raises(ScoringUnavailable):
        scorer.score(txn, {})


def test_e05_rejects_unpinned_artifacts(e05_settings, sample):
    for field in ("e05_data_digest", "scoring_model_digest"):
        cfg = e05_settings.model_copy(update={field: "0" * 64})
        with pytest.raises(ScoringUnavailable):
            E05Scorer(cfg).score(as_transaction(sample), {})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org",
        "http://localhost.evil.test",
        "http://0.0.0.0:11434",
        "http://user@127.0.0.1:11434",
        "http://127.0.0.1/redirect",
    ],
)
def test_agent_rejects_nonlocal_and_ambiguous_destinations(signing_keys, url):
    with pytest.raises(ValidationError):
        config(signing_keys, agent_url=url)


def test_agent_uses_scoped_evidence_and_structured_output(signing_keys):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"]})
        payload = json.loads(request.content)
        assert "tools" not in payload and payload["stream"] is False
        assert payload["format"]["additionalProperties"] is False
        return httpx.Response(
            200, json={"done": True, "message": {"content": json.dumps(analysis(["one"]))}}
        )

    agent = LocalAgent(config(signing_keys, agent_enabled=True), httpx.MockTransport(handler))
    result = agent.analyse({"transactions": [{"id": "one"}]})
    assert result.recommended_action == "VERIFY_WITH_CUSTOMER"
    assert all(r.url.host == "127.0.0.1" for r in seen)


@pytest.mark.parametrize(
    "failure", ["cloud", "redirect", "foreign_evidence", "invalid", "tools", "oversize"]
)
def test_agent_rejects_unsafe_or_invalid_responses(signing_keys, failure):
    def handler(request):
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"remote_host": "https://cloud.example"} if failure == "cloud" else {}
            )
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://cloud.example"})
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        content = (
            "invalid"
            if failure == "invalid"
            else json.dumps(analysis(["other" if failure == "foreign_evidence" else "one"]))
        )
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"content": content, "tool_calls": [1] if failure == "tools" else []},
            },
        )

    agent = LocalAgent(config(signing_keys, agent_enabled=True), httpx.MockTransport(handler))
    with pytest.raises(AgentUnavailable):
        agent.analyse({"transactions": [{"id": "one"}]})


def test_agent_has_total_timeout_and_releases_busy_slot(signing_keys):
    async def handler(request):
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    agent = LocalAgent(
        config(signing_keys, agent_enabled=True, agent_timeout_seconds=1),
        httpx.MockTransport(handler),
    )
    with pytest.raises(AgentUnavailable):
        agent.analyse({"transactions": [{"id": "one"}]})
    assert agent._slot.acquire(blocking=False)
    agent._slot.release()


def test_agent_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        Analysis.model_validate(analysis(["one"]) | {"confidence": float("nan")})
