"""The operator check must work with one explicit config, independent of checkout location."""

import os
import runpy
import sys
from pathlib import Path

import pytest


def test_verification_uses_supplied_configuration(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_database.py"
    for key in os.environ:
        if key.startswith("ATM_"):
            monkeypatch.delenv(key)
    config = tmp_path / "application.env"
    config.write_text(
        "ATM_DATABASE_URL=postgresql+psycopg://example:dummy@localhost/postgres?sslmode=verify-full\n"
        "ATM_ISSUER=https://example.supabase.co/auth/v1\n"
        "ATM_JWKS_URL=https://example.supabase.co/auth/v1/.well-known/jwks.json\n"
        "ATM_AUDIENCE=authenticated\n"
        "ATM_BROWSER_ORIGIN=https://localhost:8443\n"
        'ATM_JWT_ALGORITHMS=["ES256"]\n'
        'ATM_ALLOWED_HOSTS=["localhost"]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(script), "--env-file", str(config)])

    class ConfigurationChecked(Exception):
        pass

    def check_settings(settings):
        assert settings.issuer == "https://example.supabase.co/auth/v1"
        assert settings.jwt_algorithms == ["ES256"]
        assert settings.allowed_hosts == ["localhost"]
        assert settings.database_url.get_secret_value().endswith("sslmode=verify-full")
        raise ConfigurationChecked

    monkeypatch.setattr("atm_backend.db.database", check_settings)
    with pytest.raises(ConfigurationChecked):
        runpy.run_path(str(script), run_name="__main__")
