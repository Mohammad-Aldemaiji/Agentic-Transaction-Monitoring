import os
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from atm_backend.db import install_disconnect_recovery
from atm_backend.main import create_app
from atm_backend.models import Base, Principal, now
from atm_backend.settings import Settings


@pytest.fixture(scope="session")
def signing_keys(tmp_path_factory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path_factory.mktemp("keys") / "public.pem"
    path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return key, path


@pytest.fixture(scope="session")
def migration_engine():
    url = os.environ.get("ATM_TEST_MIGRATION_URL", "")
    if not url or not (make_url(url).username or "").startswith("atm_test_owner."):
        pytest.fail("Tests require the dedicated Supabase atm_test_owner login")
    engine = create_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": 5,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "tcp_user_timeout": 15000,
        },
        execution_options={"schema_translate_map": {"atm": "atm_test"}},
    )
    install_disconnect_recovery(engine)
    with engine.connect() as c:
        assert c.scalar(text("SELECT current_user")) == "atm_test_owner"
        assert not c.scalar(text("SELECT has_schema_privilege(current_user, 'atm', 'USAGE')"))
    yield engine
    engine.dispose()


@pytest.fixture
def env(migration_engine, signing_keys):
    # The named, isolated test database is required; never clear a developer/deployment database.
    tables = ", ".join("atm_test." + table.name for table in Base.metadata.sorted_tables)
    with migration_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    key, path = signing_keys
    settings = Settings(
        environment="test",
        database_schema="atm_test",
        auth_provider="oidc",
        database_url=os.environ["ATM_TEST_DATABASE_URL"],
        issuer="https://test.example",
        public_key_file=path,
        scoring_mode="simulation",
        requests_per_minute=10000,
    )
    app = create_app(settings)
    factory = sessionmaker(migration_engine, expire_on_commit=False)
    principals = {}
    with factory.begin() as s:
        for name, role, team in [
            ("analyst", "analyst", "alpha"),
            ("analyst2", "analyst", "alpha"),
            ("supervisor", "supervisor", "alpha"),
            ("auditor", "auditor", "alpha"),
            ("admin", "admin", "alpha"),
            ("ingestion", "ingestion", "alpha"),
            ("participant", "participant", "alpha"),
            ("participant2", "participant", "alpha"),
            ("outsider", "supervisor", "beta"),
        ]:
            row = Principal(
                id=uuid4(),
                issuer=settings.issuer,
                subject=name,
                role=role,
                team=team,
                valid_after=now() - timedelta(minutes=1),
            )
            s.add(row)
            principals[name] = row.id
        s.flush()

    def headers(name="analyst", **changes):
        timestamp = int(now().timestamp())
        claims = {
            "iss": settings.issuer,
            "aud": settings.audience,
            "sub": name,
            "iat": timestamp,
            "nbf": timestamp,
            "exp": timestamp + 600,
            "auth_time": timestamp,
            "amr": ["mfa"],
        }
        claims.update(changes)
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256")}

    with TestClient(app) as client:
        yield {
            "app": app,
            "client": client,
            "headers": headers,
            "ids": principals,
            "factory": factory,
            "runtime": app.state.factory,
            "settings": settings,
        }


@pytest.fixture
def transaction_body():
    return {
        "external_id": str(uuid4()),
        "account_id": "synthetic-account",
        "amount_minor": 12345,
        "currency": "SAR",
        "occurred_at": "2026-01-01T10:00:00Z",
        "channel": "TRANSFER",
    }
