"""Opaque browser sessions: no provider access/refresh tokens in browser storage."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request, Response
from sqlalchemy.orm import Session

from atm_backend.models import BrowserSession, now
from atm_backend.security import Actor, Problem, TokenVerifier

COOKIE = "atm_session"


def cookie_name(request: Request) -> str:
    return "atm_booth_session" if request.app.state.settings.environment == "booth" else COOKIE


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate(request: Request, s: Session) -> Actor:
    token = request.cookies.get(cookie_name(request), "")
    if not token or len(token) > 128:
        raise Problem(401, "authentication_required")
    row = s.get(BrowserSession, digest(token))
    if not row or row.revoked or min(row.expires_at, row.idle_expires_at) <= now():
        raise Problem(401, "session_expired")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("x-csrf-token", "")
        if request.headers.get(
            "origin"
        ) != request.app.state.settings.browser_origin or not secrets.compare_digest(
            row.csrf_hash, digest(csrf)
        ):
            raise Problem(403, "csrf_failed")
    verifier: TokenVerifier = request.app.state.verifier
    actor = verifier.from_claims(s, row.claims, request.state.request_id)
    row.idle_expires_at = (
        row.expires_at
        if request.app.state.settings.environment == "booth"
        else min(row.expires_at, now() + timedelta(minutes=15))
    )
    return actor


def establish(request: Request, response: Response, s: Session, actor: Actor) -> dict[str, Any]:
    # Require an explicit bearer credential and same-origin request to prevent login CSRF.
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise Problem(401, "bearer_required")
    if request.headers.get("origin") != request.app.state.settings.browser_origin:
        raise Problem(403, "origin_denied")
    claims = request.app.state.verifier.decode(authorization[7:])
    previous = s.get(BrowserSession, digest(request.cookies.get(cookie_name(request), "")))
    if previous:
        previous.revoked = True
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    expires = datetime.fromtimestamp(claims["exp"], UTC)
    # Store only claims required for authorization/revocation, never email or user_metadata.
    safe_claims = {
        key: claims[key]
        for key in ("iss", "sub", "iat", "exp", "auth_time", "amr", "aal", "session_id")
        if key in claims
    }
    s.add(
        BrowserSession(
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            principal_id=actor.id,
            claims=safe_claims,
            expires_at=expires,
            idle_expires_at=min(expires, now() + timedelta(minutes=15)),
        )
    )
    response.set_cookie(
        cookie_name(request),
        token,
        secure=request.app.state.settings.environment != "test",
        httponly=True,
        samesite="strict",
        path="/api/v1",
        max_age=max(1, int((expires - now()).total_seconds())),
    )
    return {"csrf_token": csrf, "expires_at": expires}


def logout(request: Request, response: Response, s: Session) -> None:
    row = s.get(BrowserSession, digest(request.cookies.get(cookie_name(request), "")))
    if row:
        row.revoked = True
    response.delete_cookie(
        cookie_name(request),
        path="/api/v1",
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.environment != "test",
    )
