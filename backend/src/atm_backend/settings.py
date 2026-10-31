from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ATM_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        secrets_dir="/run/secrets" if Path("/run/secrets").is_dir() else None,
    )

    environment: Literal["test", "shared", "booth"] = "shared"
    database_schema: Literal["atm", "atm_test", "atm_booth"] = "atm"
    database_url: SecretStr
    issuer: str
    audience: str = "atm-api"
    auth_provider: Literal["oidc", "supabase"] = "supabase"
    jwt_algorithms: list[Literal["RS256", "ES256"]] = ["RS256"]
    browser_origin: str = "http://localhost:8000"
    jwks_url: str | None = None
    public_key_file: Path | None = None
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    scoring_mode: Literal["disabled", "simulation", "remote", "e05_sample"] = "disabled"
    e05_model_dir: Path | None = None
    e05_data_file: Path | None = None
    e05_data_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    sample_traffic_enabled: bool = False
    evaluation_report_file: Path | None = None
    agent_enabled: bool = False
    agent_url: str = "http://127.0.0.1:11434"
    agent_model: str = Field(default="qwen3:8b", min_length=1, max_length=100)
    agent_timeout_seconds: float = Field(default=90, ge=1, le=120)
    piper_voice_dir: Path | None = None
    whisper_model_dir: Path | None = None
    scoring_url: str | None = None
    scoring_token: SecretStr | None = None
    scoring_model_name: str | None = Field(default=None, min_length=1, max_length=100)
    scoring_model_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    scoring_timeout_seconds: float = Field(default=5, ge=0.1, le=10)
    require_mfa: bool = True
    max_token_seconds: int = Field(default=900, ge=60, le=3600)
    approval_auth_age_seconds: int = Field(default=300, ge=30, le=600)
    request_limit_bytes: int = Field(default=131072, ge=4096, le=1048576)
    requests_per_minute: int = Field(default=300, ge=10, le=10000)
    pool_size: int = Field(default=5, ge=1, le=30)
    metrics_enabled: bool = False
    metrics_port: int = Field(default=9100, ge=1024, le=65535)
    call_timeout_seconds: int = Field(default=600, ge=30, le=1800)

    @model_validator(mode="after")
    def secure_configuration(self) -> "Settings":
        if self.scoring_mode == "e05_sample" and (
            not self.e05_model_dir
            or not self.e05_data_file
            or not self.e05_data_digest
            or not self.scoring_model_digest
            or self.scoring_model_name != "fraud_e05"
        ):
            raise ValueError("E05 requires pinned model and synthetic dataset paths and digests")
        local = urlparse(self.agent_url)
        if (
            local.scheme != "http"
            or local.hostname not in {"127.0.0.1", "::1"}
            or local.username
            or local.password
            or local.query
            or local.fragment
            or local.path not in {"", "/"}
            or "cloud" in self.agent_model.lower()
        ):
            raise ValueError("The agent requires a loopback-only local Ollama model")
        if self.scoring_mode == "remote":
            remote = urlparse(self.scoring_url or "")
            if (
                remote.scheme != "https"
                or not remote.hostname
                or remote.username
                or remote.password
                or remote.query
                or remote.fragment
                or not self.scoring_token
                or not self.scoring_token.get_secret_value()
                or not self.scoring_model_name
                or not self.scoring_model_digest
            ):
                raise ValueError(
                    "Remote scoring requires a fixed HTTPS URL, secret and model identity"
                )
        if self.environment == "booth":
            from sqlalchemy.engine import make_url

            url = make_url(self.database_url.get_secret_value())
            if (
                url.host != "127.0.0.1"
                or url.database != "atm_booth"
                or self.database_schema != "atm_booth"
                or self.auth_provider != "oidc"
                or self.issuer != "https://booth.local"
                or self.require_mfa
                or self.browser_origin != "https://localhost:8555"
                or self.allowed_hosts != ["localhost"]
            ):
                raise ValueError(
                    "Booth mode requires the isolated loopback demo database and origin"
                )
        if self.environment == "test" and self.database_schema != "atm_test":
            raise ValueError("Tests require the isolated Supabase test schema")
        if self.environment == "shared" and (
            self.database_schema != "atm" or self.auth_provider != "supabase"
        ):
            raise ValueError("The application requires Supabase Auth and the private atm schema")
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with psycopg is required")
        if bool(self.jwks_url) == bool(self.public_key_file):
            raise ValueError("Configure exactly one trusted JWT verification source")
        if self.jwks_url and urlparse(self.jwks_url).scheme != "https":
            raise ValueError("JWKS requires HTTPS")
        if "*" in self.allowed_hosts:
            raise ValueError("Explicit allowed hosts are required")
        if self.auth_provider == "supabase":
            if self.audience != "authenticated":
                raise ValueError("Supabase requires authenticated audience")
            if self.jwks_url != self.issuer.rstrip("/") + "/.well-known/jwks.json":
                raise ValueError("Use the trusted Supabase issuer JWKS endpoint")
        if self.environment == "shared":
            if not self.browser_origin.startswith("https://"):
                raise ValueError("Shared browser origin requires HTTPS")
            if parse_qs(urlparse(self.database_url.get_secret_value()).query).get("sslmode") != [
                "verify-full"
            ]:
                raise ValueError("Shared database connections require verified TLS")
            if self.public_key_file or not self.require_mfa:
                raise ValueError("Shared mode requires external JWKS and MFA")
            if urlparse(self.issuer).scheme != "https" or self.scoring_mode == "simulation":
                raise ValueError(
                    "Shared mode requires HTTPS issuer and disables simulation scoring"
                )
        return self
