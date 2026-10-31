import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from atm_backend.adapters import ScoringUnavailable
from atm_backend.contracts import TransactionInput
from atm_backend.models import Transaction, now
from atm_backend.scoring import RemoteScorer
from atm_backend.settings import Settings


def config(signing_keys, **overrides):
    args = dict(
        environment="test",
        database_schema="atm_test",
        auth_provider="oidc",
        database_url="postgresql+psycopg://unused/test",
        issuer="https://test.example",
        public_key_file=signing_keys[1],
        scoring_mode="remote",
        scoring_url="https://scorer.example/score",
        scoring_token="test-only",
        scoring_model_name="approved-v1",
        scoring_model_digest="a" * 64,
    )
    return Settings(**(args | overrides))


def transaction():
    return Transaction(
        id=uuid4(),
        external_id="synthetic",
        account_id="synthetic",
        amount_minor=100,
        currency="SAR",
        channel="TRANSFER",
        occurred_at=now(),
        context={},
    )


def result(t):
    return {
        "transaction_id": str(t.id),
        "feature_version": "account-windows-v1",
        "model_version": "approved-v1",
        "model_digest": "a" * 64,
        "supervised_probability": 0.8,
        "anomaly_score": 0.6,
        "risk_score": 0.75,
        "requires_review": False,
        "contributions": [
            {
                "feature": f"feature_{i}",
                "value": 1.0,
                "direction": "increases_risk",
                "magnitude": 1.0 - i / 10,
            }
            for i in range(5)
        ],
    }


def test_remote_provider_contract_sends_computed_features_and_binds_identity(signing_keys):
    t = transaction()

    def handle(request):
        assert request.url == "https://scorer.example/score"
        payload = json.loads(request.content)
        assert payload["features"]["version"] == "account-windows-v1"
        assert payload["transaction"]["amount_minor"] == 100
        assert "team" not in payload["transaction"]
        assert request.headers["Idempotency-Key"] == str(t.id)
        return httpx.Response(200, json=result(t))

    scorer = RemoteScorer(config(signing_keys), transport=httpx.MockTransport(handle))
    try:
        score = scorer.score(t, {"version": "account-windows-v1"})
        assert score.risk_score == 0.75 and score.simulation is False
        assert score.supervised_probability == 0.8 and len(score.contributions) == 5
    finally:
        scorer.close()


@pytest.mark.parametrize(
    "change",
    [
        {"transaction_id": str(uuid4())},
        {"model_digest": "b" * 64},
        {"model_version": "unapproved"},
        {"risk_score": True},
        {"requires_review": "yes"},
        {"contributions": []},
        {"feature_version": "unknown"},
        {"extra": "injected"},
        {"anomaly_score": 2.0},
    ],
)
def test_invalid_provider_result_fails_closed(signing_keys, change):
    t = transaction()
    scorer = RemoteScorer(
        config(signing_keys),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result(t) | change)),
    )
    try:
        with pytest.raises(ScoringUnavailable):
            scorer.score(t, {"version": "account-windows-v1"})
    finally:
        scorer.close()


@pytest.mark.parametrize("mode", ["redirect", "timeout", "oversized", "malformed", "compressed"])
def test_remote_provider_bounds_and_failures(signing_keys, mode):
    def handle(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive provider message", request=request)
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://unapproved.example"})
        if mode == "compressed":
            import gzip

            return httpx.Response(
                200, content=gzip.compress(b"{}"), headers={"Content-Encoding": "gzip"}
            )
        return httpx.Response(200, content=b"x" * (70000 if mode == "oversized" else 10))

    scorer = RemoteScorer(config(signing_keys), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(ScoringUnavailable) as error:
            scorer.score(transaction(), {"version": "account-windows-v1"})
        assert "sensitive provider message" not in str(error.value)
    finally:
        scorer.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://scorer.example",
        "https://user:password@scorer.example",
        "https://scorer.example/?key=secret",
        "https://scorer.example/#fragment",
    ],
)
def test_provider_destination_is_operator_fixed_https(signing_keys, url):
    with pytest.raises(ValidationError):
        config(signing_keys, scoring_url=url)


@pytest.mark.parametrize(
    "context",
    [
        {"device_id": "bad space"},
        {"merchant_category": "ABC"},
        {"location_country": "sa"},
        {"risk_score": 0.0},
    ],
)
def test_transaction_context_is_strict(transaction_body, context):
    with pytest.raises(ValidationError):
        TransactionInput(**transaction_body, context=context)


def test_total_deadline_stops_slow_drip_response(signing_keys):
    import asyncio
    import time

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.04)
                yield b" "

    scorer = RemoteScorer(
        config(signing_keys, scoring_timeout_seconds=0.1),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=SlowBody())),
    )
    started = time.monotonic()
    try:
        with pytest.raises(ScoringUnavailable):
            scorer.score(transaction(), {"version": "account-windows-v1"})
        assert time.monotonic() - started < 1
    finally:
        scorer.close()
