import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from atm_backend.models import now
from atm_backend.sample_traffic import evaluation
from atm_backend.security import Actor, Problem

spec = importlib.util.spec_from_file_location(
    "evaluate_e05", Path(__file__).parents[1] / "scripts/evaluate_e05.py"
)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_metric_counts_expose_misses_and_false_alarms():
    result = study.metrics([1, 1, 0, 0], [0.9, 0.1, 0.8, 0.2], 0.5)
    assert (
        result["caught"],
        result["missed"],
        result["false_alarms"],
        result["true_negatives"],
    ) == (1, 1, 1, 1)
    assert result["recall"] == result["precision"] == result["false_positive_rate"] == 0.5


def test_threshold_uses_validation_objective_without_test_inputs():
    threshold = study.choose_threshold([1, 1, 0, 0], [0.9, 0.7, 0.2, 0.1])
    assert threshold == 0.7
    # A later miss must not silently retune the chosen operating point.
    assert study.metrics([1, 0], [0.3, 0.1], threshold)["missed"] == 1


def test_report_rejects_unmatched_artifacts_and_unauthorized_role(tmp_path, signing_keys):
    from atm_backend.settings import Settings

    path = tmp_path / "report.json"
    settings = Settings(
        environment="test",
        database_schema="atm_test",
        auth_provider="oidc",
        database_url="postgresql+psycopg://localhost/test",
        issuer="https://test.example",
        public_key_file=signing_keys[1],
        sample_traffic_enabled=True,
        evaluation_report_file=path,
        scoring_model_digest="a" * 64,
        e05_data_digest="b" * 64,
    )
    actor = Actor(uuid4(), "team", "analyst", now(), "test")
    path.write_text(json.dumps({"model_digest": "a" * 64, "data_digest": "b" * 64}))
    assert evaluation(actor, settings)["data_digest"] == "b" * 64
    path.write_text("{}")
    with pytest.raises(Problem) as error:
        evaluation(actor, settings)
    assert error.value.status == 503
    with pytest.raises(Problem) as error:
        evaluation(Actor(uuid4(), "team", "participant", now(), "test"), settings)
    assert error.value.status == 403
