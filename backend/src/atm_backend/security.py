"""JWT authentication plus authoritative, revocable database permissions."""

import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import certifi
import jwt
from jwt import PyJWKClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from atm_backend.models import Principal, now
from atm_backend.settings import Settings


class Problem(Exception):
    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Actor:
    id: UUID
    team: str
    role: str
    auth_time: datetime
    request_id: str

    def require(self, *roles: str) -> None:
        if self.role not in roles:
            raise Problem(403, "permission_denied")


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.local_key = settings.public_key_file.read_text() if settings.public_key_file else None
        self.jwks = (
            PyJWKClient(
                settings.jwks_url,
                timeout=3,
                lifespan=300,
                ssl_context=ssl.create_default_context(cafile=certifi.where()),
            )
            if settings.jwks_url
            else None
        )

    def decode(self, token: str) -> dict[str, Any]:
        if len(token) > 8192:
            raise Problem(401, "invalid_token")
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key if self.jwks else self.local_key
            if key is None:
                raise ValueError("Verification key unavailable")
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=self.settings.jwt_algorithms,
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if self.settings.auth_provider == "supabase":
                if claims.get("role") != "authenticated" or claims.get("is_anonymous") is not False:
                    raise ValueError("Authenticated non-anonymous user required")
                UUID(claims["session_id"])
                methods = claims.get("amr", [])
                times = [
                    m["timestamp"]
                    for m in methods
                    if isinstance(m, dict)
                    and m.get("method") in ("totp", "password", "oauth", "sso/saml", "otp")
                    and type(m.get("timestamp")) is int
                ]
                if claims.get("aal") == "aal2":
                    times = [
                        m["timestamp"]
                        for m in methods
                        if isinstance(m, dict)
                        and m.get("method") == "totp"
                        and type(m.get("timestamp")) is int
                    ]
                if not times:
                    raise ValueError("Authentication evidence required")
                claims["auth_time"] = max(times)
                claims["nbf"] = claims.get("nbf", claims["iat"])
            for claim in ("exp", "iat", "nbf", "auth_time"):
                if type(claims[claim]) is not int:
                    raise ValueError("Invalid timestamp")
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 200:
                raise ValueError("Invalid subject")
            if not 0 < claims["exp"] - claims["iat"] <= self.settings.max_token_seconds:
                raise ValueError("Token lifetime exceeds policy")
            if claims["auth_time"] > claims["iat"]:
                raise ValueError("Invalid authentication time")
            return claims
        except jwt.PyJWKClientConnectionError:
            raise Problem(503, "authentication_provider_unavailable") from None
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise Problem(401, "invalid_token") from None

    def authenticate(self, session: Session, token: str, request_id: str) -> Actor:
        return self.from_claims(session, self.decode(token), request_id)

    def from_claims(self, session: Session, claims: dict[str, Any], request_id: str) -> Actor:
        if claims["exp"] <= now().timestamp():
            raise Problem(401, "session_expired")
        return self.authorize_claims(session, claims, request_id)

    def authorize_claims(self, session: Session, claims: dict[str, Any], request_id: str) -> Actor:
        """Recheck identity/revocation for already verified, server-stored session claims.

        The caller must enforce its own bounded opaque-session lifetime. Bearer
        authentication always uses decode/from_claims, including token expiry.
        """
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == self.settings.issuer,
                Principal.subject == claims["sub"],
            )
        )
        if (
            not principal
            or not principal.active
            or claims["iat"] < principal.valid_after.timestamp()
        ):
            raise Problem(401, "identity_inactive")
        if self.settings.require_mfa and principal.role != "ingestion":
            amr = claims.get("amr", [])
            mfa = (
                claims.get("aal") == "aal2"
                if self.settings.auth_provider == "supabase"
                else (isinstance(amr, list) and "mfa" in amr)
            )
            if not mfa:
                raise Problem(403, "mfa_required")
        if self.settings.auth_provider == "supabase":
            # Only server-verified claims reach this boundary. Keep the subject
            # transaction-local so pooled connections cannot retain another user.
            session.execute(
                text("SELECT set_config('request.jwt.claim.sub', :subject, true)"),
                {"subject": str(UUID(claims["sub"]))},
            )
            active = session.scalar(
                text("SELECT atm.provider_session_active(:id)"),
                {"id": UUID(claims["session_id"])},
            )
            if not active:
                raise Problem(401, "provider_session_revoked")
        return Actor(
            principal.id,
            principal.team,
            principal.role,
            datetime.fromtimestamp(claims["auth_time"], now().tzinfo),
            request_id,
        )
