from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_workflows import post, submit

from atm_backend.adapters import SimulationScorer
from atm_backend.models import Case, Transaction
from atm_backend.worker import run_one


def propose(env, **changes):
    return post(
        env,
        "admin/risk-policy",
        {
            "threshold": 0.4,
            "reason": "Synthetic review capacity policy",
            "daily_review_capacity": 1,
            "medium_threshold": 0.1,
            "high_threshold": 0.2,
            "critical_threshold": 0.3,
            **changes,
        },
        "admin",
    )


def test_capacity_policy_preserves_cases_records_bands_and_requires_new_version(
    env, transaction_body
):
    first = propose(env)
    assert first.status_code == 201
    policy_id = first.json()["id"]
    assert post(env, f"admin/risk-policy/{policy_id}/activate", {}, "supervisor").status_code == 200
    for index in range(2):
        assert (
            submit(
                env, {**transaction_body, "external_id": f"capacity-{index}"}, f"capacity-{index}"
            ).status_code
            == 202
        )
        assert run_one(env["runtime"], SimulationScorer())
    response = env["client"].get("/api/v1/dashboard", headers=env["headers"]("supervisor"))
    assert response.status_code == 200
    capacity = response.json()["review_capacity"]
    assert capacity["target"] == 1
    assert capacity["opened_today"] == 2 and capacity["over_target"] is True
    assert capacity["suppresses_alerts"] is False
    assert response.json()["open_severity"] == {"CRITICAL": 2}
    traffic = response.json()["transactions"]
    assert traffic["received"] == traffic["received_today"] == traffic["received_last_hour"] == 2
    assert traffic["scored"] == 2 and traffic["pending"] == 0 and traffic["failed"] == 0
    assert traffic["flagged"] == traffic["flagged_last_hour"] == 2
    assert traffic["flag_rate"] == 1.0
    assert 0 <= traffic["average_risk_score"] <= 100
    assert traffic["last_received_at"] and response.json()["generated_at"]
    assert response.json()["decided_today"] == 0
    # Chart series: 14 UTC days of flagged cases by band, 7 days of decisions.
    trends = response.json()["trends"]
    assert len(trends["days"]) == len(trends["flagged_by_day"]) == 14
    assert trends["flagged_by_day"][-1]["CRITICAL"] == 2
    assert sum(sum(day.values()) for day in trends["flagged_by_day"]) == 2
    assert trends["decided_by_day"] == [0] * 7
    assert trends["decision_hours_by_day"] == [None] * 7
    assert sum(traffic["score_bins_today"]) == 2 and len(traffic["score_bins_today"]) == 10
    assert response.json()["review_threshold"] is not None
    analyst = env["client"].get("/api/v1/dashboard", headers=env["headers"]())
    assert analyst.json()["review_capacity"] is None
    # Outside the booth, analysts keep case-scoped numbers only (no team-wide traffic).
    assert analyst.json()["transactions"] is None
    queue = env["client"].get("/api/v1/cases?severity=CRITICAL", headers=env["headers"]())
    assert len(queue.json()["items"]) == 2
    assert all(row["severity_policy"] == "risk-policy-v1" for row in queue.json()["items"])
    with env["factory"]() as s:
        cases = list(s.scalars(select(Case)))
        assert len(cases) == 2
        for row in s.scalars(select(Transaction)):
            assert row.result["daily_review_capacity"] == 1
            assert row.result["severity_thresholds"]["critical"] == 0.3
    for statement in (
        "UPDATE atm_test.risk_policies SET threshold = 0.9 WHERE id = :id",
        "UPDATE atm_test.risk_policies SET daily_review_capacity = 100 WHERE id = :id",
        "UPDATE atm_test.risk_policies SET state = 'DRAFT', approved_by = NULL WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as error, env["runtime"].begin() as s:
            s.execute(text(statement), {"id": UUID(policy_id)})
        assert error.value.orig.sqlstate == "23514"
    second = propose(env, critical_threshold=0.9, high_threshold=0.8)
    assert second.status_code == 201
    assert (
        post(env, f"admin/risk-policy/{second.json()['id']}/activate", {}, "supervisor").status_code
        == 200
    )
    old = env["client"].get("/api/v1/cases?severity=CRITICAL", headers=env["headers"]())
    assert len(old.json()["items"]) == 2  # Historical severity must not be reinterpreted.
    assert all(row["severity_policy"] == "risk-policy-v1" for row in old.json()["items"])


def test_invalid_capacity_and_band_order_rejected(env):
    for changes in (
        {"daily_review_capacity": True},
        {"daily_review_capacity": 0},
        {"daily_review_capacity": 100001},
        {"medium_threshold": 0.3},
        {"critical_threshold": 0.1},
        {"high_threshold": 1.01},
    ):
        assert propose(env, **changes).status_code == 422
    assert propose(env, daily_review_capacity=None).status_code == 201
