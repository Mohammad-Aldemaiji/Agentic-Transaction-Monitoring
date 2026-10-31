import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from atm_backend.contracts import ApprovalInput
from atm_backend.security import Problem, TokenVerifier
from atm_backend.settings import Settings


@given(st.one_of(st.integers(), st.text(), st.none(), st.floats(allow_nan=True)))
def test_approval_requires_actual_boolean(value):
    with pytest.raises(ValidationError):
        ApprovalInput(expected_version=1, approved=value)


def test_shared_profile_rejects_local_keys_and_unverified_database(signing_keys):
    with pytest.raises(ValidationError):
        Settings(
            environment="shared",
            database_url="postgresql+psycopg://localhost/atm",
            issuer="https://id.example",
            public_key_file=signing_keys[1],
        )


def supabase_verifier():
    settings = Settings(
        environment="test",
        database_schema="atm_test",
        database_url="postgresql+psycopg://localhost/atm",
        issuer="https://example.supabase.co/auth/v1",
        audience="authenticated",
        auth_provider="supabase",
        jwt_algorithms=["ES256"],
        jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
    )
    verifier = TokenVerifier(settings)
    key = ec.generate_private_key(ec.SECP256R1())
    verifier.jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())
    )
    timestamp = int(time.time())
    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": str(uuid4()),
        "iat": timestamp,
        "exp": timestamp + 600,
        "session_id": str(uuid4()),
        "is_anonymous": False,
        "role": "authenticated",
        "aal": "aal2",
        "amr": [
            {"method": "password", "timestamp": timestamp - 300},
            {"method": "totp", "timestamp": timestamp - 60},
        ],
    }
    return verifier, key, claims


def test_supabase_uses_mfa_timestamp_not_token_refresh_for_recent_auth():
    verifier, key, claims = supabase_verifier()
    decoded = verifier.decode(jwt.encode(claims, key, algorithm="ES256"))
    assert decoded["auth_time"] == claims["amr"][1]["timestamp"]
    assert decoded["auth_time"] != decoded["iat"]


@pytest.mark.parametrize(
    "change",
    [
        {"is_anonymous": True},
        {"role": "service_role"},
        {"session_id": "invalid"},
        {"amr": []},
        {"amr": [{"method": "token_refresh", "timestamp": 1}]},
    ],
)
def test_supabase_invalid_claims_fail_closed(change):
    verifier, key, claims = supabase_verifier()
    claims.update(change)
    with pytest.raises(Problem):
        verifier.decode(jwt.encode(claims, key, algorithm="ES256"))


@pytest.mark.parametrize("active", [True, False, None])
def test_supabase_session_boundary_binds_verified_subject_and_fails_closed(active):
    verifier, key, claims = supabase_verifier()
    decoded = verifier.decode(jwt.encode(claims, key, algorithm="ES256"))
    principal = SimpleNamespace(
        id=uuid4(),
        team="project",
        role="supervisor",
        active=True,
        valid_after=datetime.fromtimestamp(0, UTC),
    )
    session = Mock()
    session.scalar.side_effect = [principal, active]
    if active:
        assert verifier.from_claims(session, decoded, "test").id == principal.id
    else:
        with pytest.raises(Problem) as error:
            verifier.from_claims(session, decoded, "test")
        assert error.value.status == 401
        assert error.value.code == "provider_session_revoked"
    context = session.execute.call_args
    assert str(context.args[0]) == "SELECT set_config('request.jwt.claim.sub', :subject, true)"
    assert context.args[1] == {"subject": claims["sub"]}
    lookup = session.scalar.call_args
    assert str(lookup.args[0]) == "SELECT atm.provider_session_active(:id)"
    assert lookup.args[1] == {"id": UUID(claims["session_id"])}
    assert session.mock_calls[1][0] == "execute"


def test_signing_key_connection_failure_is_service_unavailable():
    verifier, key, claims = supabase_verifier()
    verifier.jwks = Mock()
    verifier.jwks.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientConnectionError("TLS")
    with pytest.raises(Problem) as error:
        verifier.decode("unavailable")
    assert error.value.status == 503
    assert error.value.code == "authentication_provider_unavailable"
