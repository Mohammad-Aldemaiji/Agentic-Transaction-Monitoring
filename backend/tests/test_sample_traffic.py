from unittest.mock import Mock
from uuid import uuid4

import pytest
import test_local_ai as fixtures
from sqlalchemy import func, select

from atm_backend.contracts import SampleTrafficInput
from atm_backend.models import Transaction, now
from atm_backend.sample_traffic import replay
from atm_backend.security import Actor, Problem

e05_settings = fixtures.e05_settings


@pytest.mark.parametrize("role", ["auditor", "participant", "ingestion"])
def test_sample_replay_rejects_other_roles(e05_settings, role):
    actor = Actor(uuid4(), "alpha", role, now(), "test")
    with pytest.raises(Problem) as error:
        replay(Mock(), actor, e05_settings, SampleTrafficInput(offset=0))
    assert error.value.status == 403


def test_sample_replay_disabled_and_wrong_digest_fail_closed(e05_settings):
    actor = Actor(uuid4(), "alpha", "analyst", now(), "test")
    with pytest.raises(Problem) as error:
        replay(Mock(), actor, e05_settings, SampleTrafficInput(offset=0))
    assert error.value.status == 404
    e05_settings.sample_traffic_enabled = True
    e05_settings.e05_data_digest = "0" * 64
    with pytest.raises(Problem) as error:
        replay(Mock(), actor, e05_settings, SampleTrafficInput(offset=0))
    assert error.value.status == 503


def test_sample_endpoint_is_bounded_idempotent_and_team_scoped(env, e05_settings):
    settings = env["client"].app.state.settings
    settings.sample_traffic_enabled = True
    settings.e05_data_file = e05_settings.e05_data_file
    settings.e05_data_digest = e05_settings.e05_data_digest
    client = env["client"]
    for _ in range(2):
        response = client.post(
            "/api/v1/sample-traffic", json={"offset": 0}, headers=env["headers"]()
        )
        assert response.status_code == 202, response.text
        assert response.json() == {"accepted": 10}
    assert (
        client.post(
            "/api/v1/sample-traffic", json={"offset": 50}, headers=env["headers"]()
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/sample-traffic", json={"offset": 0}, headers=env["headers"]("auditor")
        ).status_code
        == 403
    )
    with env["factory"]() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 10
        assert set(session.scalars(select(Transaction.team))) == {"alpha"}
