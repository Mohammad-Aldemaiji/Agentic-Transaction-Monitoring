"""Real MFA gate for the isolated presentation's synthetic role sessions."""

from typing import Any

from fastapi import FastAPI, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from atm_backend.booth import select_role
from atm_backend.db import database
from atm_backend.main import DB
from atm_backend.models import now
from atm_backend.security import Actor, Problem, TokenVerifier
from atm_backend.settings import Settings


class PresenterVerifier(TokenVerifier):
    def __init__(self, local: Settings, provider: Settings) -> None:
        super().__init__(local)
        if provider.auth_provider != "supabase" or not provider.require_mfa:
            raise ValueError("Presenter authentication requires Supabase MFA")
        self.provider = TokenVerifier(provider)
        self.provider_engine, self.provider_factory = database(provider)

    def check_presenter(
        self, claims: dict[str, Any], request_id: str, *, persisted: bool = False
    ) -> Actor:
        if claims.get("aal") != "aal2":
            raise Problem(403, "mfa_required")
        with self.provider_factory.begin() as session:
            session.execute(text("SET TRANSACTION READ ONLY"))
            if persisted:
                if claims.get("workspace_expires_at", 0) <= now().timestamp():
                    raise Problem(401, "session_expired")
                actor = self.provider.authorize_claims(session, claims, request_id)
            else:
                actor = self.provider.from_claims(session, claims, request_id)
            actor.require("supervisor", "admin")
            return actor

    def from_claims(self, session: Session, claims: dict[str, Any], request_id: str) -> Actor:
        presenter = claims.get("presenter")
        if not isinstance(presenter, dict):
            raise Problem(401, "authentication_required")
        self.check_presenter(presenter, request_id, persisted=True)
        return super().from_claims(session, claims, request_id)


def install(app: FastAPI, provider: Settings) -> None:
    if app.state.settings.environment != "booth":
        raise ValueError("Presenter roles are restricted to the isolated presentation")
    verifier = PresenterVerifier(app.state.settings, provider)
    app.state.verifier = verifier

    @app.post("/api/v1/booth/session")
    def login(request: Request, response: Response, s: DB) -> dict[str, Any]:
        if request.headers.get("origin") != app.state.settings.browser_origin:
            raise Problem(403, "origin_denied")
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise Problem(401, "bearer_required")
        claims = verifier.provider.decode(authorization[7:])
        verifier.check_presenter(claims, request.state.request_id)
        safe = {
            key: claims[key]
            for key in ("iss", "sub", "iat", "exp", "auth_time", "amr", "aal", "session_id")
            if key in claims
        }
        safe["workspace_expires_at"] = int(now().timestamp()) + 24 * 3600
        return select_role("analyst", request, response, s, safe)
