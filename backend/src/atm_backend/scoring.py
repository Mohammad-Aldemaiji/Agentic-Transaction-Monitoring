"""Bounded HTTPS adapter for an independently approved ML service.

The deployment operator selects one destination and model; API callers cannot change
URLs, credentials, model identity or features. No provider is enabled by default.
"""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atm_backend.adapters import DisabledScorer, Score, Scorer, ScoringUnavailable, SimulationScorer
from atm_backend.features import FEATURE_VERSION
from atm_backend.models import Transaction
from atm_backend.settings import Settings

Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Finite = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Contribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature: Annotated[str, Field(min_length=1, max_length=100)]
    value: Finite
    direction: Literal["increases_risk", "decreases_risk"]
    magnitude: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: UUID
    feature_version: Literal["account-windows-v1"]
    model_version: Annotated[str, Field(min_length=1, max_length=100)]
    model_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    supervised_probability: Probability
    anomaly_score: Probability
    risk_score: Probability
    requires_review: Annotated[bool, Field(strict=True)]
    contributions: Annotated[list[Contribution], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def ordered_contributions(self) -> "ProviderResult":
        if len({c.feature for c in self.contributions}) != 5:
            raise ValueError("Five distinct contributing features are required")
        weights = [c.magnitude for c in self.contributions]
        if weights != sorted(weights, reverse=True):
            raise ValueError("Contributions must be ranked")
        return self


class RemoteScorer:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.runner = asyncio.Runner()
        self.closed = False
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.scoring_timeout_seconds, connect=2),
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def score(self, transaction: Transaction, features: dict[str, Any]) -> Score:
        cfg = self.settings
        if features.get("version") != FEATURE_VERSION:
            raise ScoringUnavailable("Feature contract mismatch")
        payload = {
            "transaction": {
                "id": str(transaction.id),
                "external_id": transaction.external_id,
                "account_id": transaction.account_id,
                "amount_minor": transaction.amount_minor,
                "currency": transaction.currency,
                "channel": transaction.channel,
                "occurred_at": transaction.occurred_at.isoformat(),
                "context": transaction.context,
            },
            "features": features,
            "model_version": cfg.scoring_model_name,
            "model_digest": cfg.scoring_model_digest,
        }
        try:
            result = self.runner.run(self._request(payload, str(transaction.id)))
        except (httpx.HTTPError, ValidationError, ValueError, TimeoutError) as error:
            raise ScoringUnavailable("Invalid or unavailable scoring response") from error
        if (
            result.transaction_id != transaction.id
            or result.model_version != cfg.scoring_model_name
            or result.model_digest != cfg.scoring_model_digest
        ):
            raise ScoringUnavailable("Scoring identity mismatch")
        return Score(
            False,
            result.model_version,
            result.risk_score,
            result.requires_review,
            [c.feature for c in result.contributions],
            supervised_probability=result.supervised_probability,
            anomaly_score=result.anomaly_score,
            contributions=[c.model_dump() for c in result.contributions],
            model_digest=result.model_digest,
        )

    async def _request(self, payload: dict[str, Any], request_id: str) -> ProviderResult:
        cfg = self.settings
        if not cfg.scoring_url or not cfg.scoring_token:
            raise ScoringUnavailable("Remote scoring configuration missing")
        # One persistent loop keeps connections reusable. The outer cancellation
        # deadline also bounds slow headers/bodies that evade per-read timeouts.
        async with asyncio.timeout(cfg.scoring_timeout_seconds):
            async with self.client.stream(
                "POST",
                cfg.scoring_url,
                json=payload,
                headers={
                    "Authorization": "Bearer " + cfg.scoring_token.get_secret_value(),
                    "Idempotency-Key": request_id,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code != 200:
                    raise ScoringUnavailable("Scoring provider unavailable")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ScoringUnavailable("Compressed scoring responses are not supported")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ScoringUnavailable("Scoring response exceeds limit")
                return ProviderResult.model_validate_json(bytes(data))

    def close(self) -> None:
        if not self.closed:
            self.runner.run(self.client.aclose())
            self.runner.close()
            self.closed = True


def configured_scorer(settings: Settings) -> Scorer:
    if settings.scoring_mode == "e05_sample":
        from atm_backend.e05 import E05Scorer

        return E05Scorer(settings)
    if settings.scoring_mode == "remote":
        return RemoteScorer(settings)
    return SimulationScorer() if settings.scoring_mode == "simulation" else DisabledScorer()
