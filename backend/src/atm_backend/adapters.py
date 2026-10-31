"""Replaceable decision contracts. Simulation is never presented as trained ML."""

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, Protocol

from atm_backend.models import Transaction


class ScoringUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Score:
    simulation: bool
    model_version: str
    risk_score: float
    requires_review: bool
    reasons: list[str]
    supervised_probability: float | None = None
    anomaly_score: float | None = None
    contributions: list[dict[str, Any]] = field(default_factory=list)
    model_digest: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            type(self.risk_score) not in (int, float)
            or not isfinite(self.risk_score)
            or not 0 <= self.risk_score <= 1
            or type(self.requires_review) is not bool
            or type(self.simulation) is not bool
            or not self.model_version
            or len(self.model_version) > 100
            or not 1 <= len(self.reasons) <= 20
            or any(not isinstance(r, str) or not 1 <= len(r) <= 500 for r in self.reasons)
        ):
            raise ScoringUnavailable("Invalid provider result")

    def json(self) -> dict[str, Any]:
        return asdict(self)


class Scorer(Protocol):
    def score(self, transaction: Transaction, features: dict[str, Any]) -> Score: ...


class DisabledScorer:
    def score(self, transaction: Transaction, features: dict[str, Any]) -> Score:
        raise ScoringUnavailable("No approved scoring provider configured")


class SimulationScorer:
    def score(self, transaction: Transaction, features: dict[str, Any]) -> Score:
        # All synthetic transactions create review cases to exercise the full workflow.
        # This is not a fraud classifier, probability estimate or automated disposition.
        return Score(True, "workflow-simulation-v1", 0.5, True, ["SIMULATION_REVIEW_REQUIRED"])
