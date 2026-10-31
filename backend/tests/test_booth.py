from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request
from test_scoring_contract import config

from atm_backend.booth import register
from atm_backend.main import create_app
from atm_backend.sessions import cookie_name
from atm_backend.settings import Settings


def test_booth_routes_are_absent_outside_isolated_profile(signing_keys):
    settings = config(signing_keys)
    app = create_app(settings)
    with pytest.raises(ValueError):
        register(app)
    with TestClient(app) as client:
        assert client.get("/api/v1/booth/config").json() == {"enabled": False}
        assert client.post("/api/v1/booth/role", json={"role": "admin"}).status_code == 404
        assert client.post("/api/v1/booth/feed", json={"count": 100}).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"database_schema": "atm"},
        {"database_url": "postgresql+psycopg://remote/atm_booth"},
        {"database_url": "postgresql+psycopg://127.0.0.1/atm"},
        {"auth_provider": "supabase"},
        {"browser_origin": "https://public.example"},
        {"allowed_hosts": ["*"]},
    ],
)
def test_booth_cannot_use_shared_storage_or_origin(signing_keys, change):
    values = config(signing_keys).model_dump()
    values.update(
        environment="booth",
        database_schema="atm_booth",
        auth_provider="oidc",
        database_url="postgresql+psycopg://127.0.0.1/atm_booth",
        issuer="https://booth.local",
        browser_origin="https://localhost:8555",
        allowed_hosts=["localhost"],
        require_mfa=False,
    )
    Settings(**values)
    values.update(change)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_booth_cookie_cannot_replace_shared_session():
    def request(environment):
        return Request(
            {
                "type": "http",
                "app": SimpleNamespace(
                    state=SimpleNamespace(settings=SimpleNamespace(environment=environment))
                ),
            }
        )

    assert cookie_name(request("booth")) == "atm_booth_session"
    assert cookie_name(request("shared")) == "atm_session"
