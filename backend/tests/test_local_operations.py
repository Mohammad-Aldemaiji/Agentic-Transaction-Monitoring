import time

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from pydantic import ValidationError

from atm_backend import healthcheck
from atm_backend.main import create_app
from atm_backend.settings import Settings


def local_settings(signing_keys, **kwargs):
    return Settings(
        environment="test",
        database_schema="atm_test",
        auth_provider="oidc",
        database_url="postgresql+psycopg://localhost/unused",
        issuer="https://test.example",
        public_key_file=signing_keys[1],
        **kwargs,
    )


def test_metrics_use_route_templates_and_never_raw_identifiers(signing_keys):
    app = create_app(local_settings(signing_keys))
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/secret-account-123?token=private-token").status_code == 404
        assert client.get("/api/v1/cases/secret-case-456").status_code == 401
        assert client.post("/api/v1/transactions", content=b"x" * 131073).status_code == 413
        metrics = generate_latest(app.state.telemetry.registry).decode()
    for private in ("secret-account", "private-token", "secret-case"):
        assert private not in metrics
    assert 'route="/api/v1/cases/{case_id}"' in metrics
    assert 'status="413"' in metrics
    assert 'route="unmatched"' in metrics


def test_database_secret_can_be_loaded_from_file(tmp_path, signing_keys, monkeypatch):
    monkeypatch.delenv("ATM_DATABASE_URL", raising=False)
    secret = "postgresql+psycopg://localhost/file_secret"
    (tmp_path / "ATM_DATABASE_URL").write_text(secret)
    settings = Settings(
        _secrets_dir=tmp_path,
        _env_file=None,
        environment="test",
        database_schema="atm_test",
        auth_provider="oidc",
        issuer="https://test.example",
        public_key_file=signing_keys[1],
    )
    assert settings.database_url.get_secret_value() == secret
    assert secret not in repr(settings)


def test_invalid_configuration_does_not_echo_credentials(signing_keys):
    with pytest.raises(ValidationError) as error:
        Settings(
            database_url="postgresql+psycopg://user:private-password@localhost/db",
            issuer="https://test.example",
            public_key_file=signing_keys[1],
        )
    assert "private-password" not in str(error.value)


@pytest.mark.parametrize("age", [0, 61, -10])
def test_worker_health_fails_when_heartbeat_is_stale(tmp_path, monkeypatch, age):
    path = tmp_path / "heartbeat"
    path.write_text(str(time.monotonic() - age))
    monkeypatch.setattr(healthcheck, "HEARTBEAT", path)
    monkeypatch.setattr("sys.argv", ["healthcheck", "worker"])
    if age == 0:
        healthcheck.main()
    else:
        with pytest.raises(SystemExit) as exc:
            healthcheck.main()
        assert exc.value.code == 1
