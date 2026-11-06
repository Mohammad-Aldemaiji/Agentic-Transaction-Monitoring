"""Individual staff access for the isolated local workspace.

Supabase owns passwords and email delivery; local authoritative principals own roles.
No role is read from user-editable provider metadata. The second sign-in factor is
server-checked face verification (see face_verification.py), which replaced the
authenticator-app code.
"""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Body, FastAPI, Request, Response
from pydantic import Field, ValidationError
from sqlalchemy import DateTime, String, delete, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from atm_backend import contracts as dto
from atm_backend import face_verification as face
from atm_backend import services, sessions
from atm_backend.db import database
from atm_backend.main import DB, Identity
from atm_backend.models import BrowserSession, Principal, now
from atm_backend.security import Actor, Problem, TokenVerifier
from atm_backend.settings import Settings

STAFF_ACTION_AUTH_SECONDS = 5 * 60 * 60


class StaffBase(DeclarativeBase):
    pass


class StaffProfile(StaffBase):
    __tablename__ = "staff_profiles"
    __table_args__ = {"schema": "atm"}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    principal_id: Mapped[UUID | None] = mapped_column(nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    version: Mapped[int] = mapped_column(default=1)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StaffFace(StaffBase):
    """Enrolled face descriptors for one staff principal (biometric data; local only)."""

    __tablename__ = "staff_faces"
    __table_args__ = {"schema": "atm"}
    principal_id: Mapped[UUID] = mapped_column(primary_key=True)
    templates: Mapped[list[list[float]]] = mapped_column(JSONB)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    failures: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InviteInput(dto.Input):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    name: str = Field(min_length=1, max_length=100)
    role: Literal["analyst", "supervisor", "admin"]


def provision_first_admin(session: Session, issuer: str, subject: UUID, email: str) -> None:
    """Local operator only: bind a previously verified provider identity once."""
    email = InviteInput(email=email, name="Administrator", role="admin").email.casefold()
    session.execute(text("LOCK TABLE atm_booth.staff_profiles IN EXCLUSIVE MODE"))
    existing = session.scalar(select(StaffProfile).where(StaffProfile.email == email))
    if existing:
        identity = session.get(Principal, existing.principal_id) if existing.principal_id else None
        if (
            identity
            and identity.active
            and identity.role == "admin"
            and identity.team == "booth"
            and identity.subject == str(subject)
            and identity.issuer == issuer
        ):
            return
        raise ValueError("Staff identity already exists; refusing to overwrite it")
    if session.scalar(select(StaffProfile.id).limit(1)):
        raise ValueError("Staff access already initialized")
    principal = Principal(
        issuer=issuer,
        subject=str(subject),
        team="booth",
        role="admin",
        valid_after=now() - timedelta(seconds=1),
    )
    session.add(principal)
    session.flush()
    session.add(
        StaffProfile(
            email=email,
            name="Administrator",
            role="admin",
            principal_id=principal.id,
            status="ACTIVE",
        )
    )
    actor = Actor(principal.id, "booth", "admin", now(), str(uuid4()))
    services.audit(session, actor, "staff.first_administrator", principal.id)


class StaffUpdate(dto.VersionInput):
    role: Literal["analyst", "supervisor", "admin"]
    active: bool


class InviteProvider:
    def __init__(self, issuer: str, key_file: Path | None, origin: str):
        if not issuer.startswith("https://") or not issuer.endswith("/auth/v1"):
            raise ValueError("A verified Supabase Auth endpoint is required")
        self.url, self.key_file, self.origin = issuer, key_file, origin
        self.transport: httpx.BaseTransport | None = None  # tests inject a mock transport

    def remove_authenticators(self, user_id: UUID) -> int:
        """Operator only: delete a user's old authenticator-app (TOTP) factors.

        Face verification replaced the authenticator step. Supabase still asks for the
        old code before a password change while such a factor exists, so remove it.
        """
        if not self.key_file or not self.key_file.is_file():
            raise Problem(503, "invitations_not_configured")
        key = self.key_file.read_text().strip()
        if not key:
            raise Problem(503, "invitations_not_configured")
        headers = {"apikey": key, "Authorization": "Bearer " + key}
        base = f"{self.url}/admin/users/{UUID(str(user_id))}/factors"
        with httpx.Client(
            timeout=15, follow_redirects=False, trust_env=False, transport=self.transport
        ) as client:
            response = client.get(base, headers=headers)
            response.raise_for_status()
            factors = response.json()
            if not isinstance(factors, list):
                raise ValueError("Unexpected factor list")
            removed = 0
            for factor in factors:
                if isinstance(factor, dict) and factor.get("factor_type") == "totp":
                    client.delete(
                        f"{base}/{UUID(str(factor['id']))}", headers=headers
                    ).raise_for_status()
                    removed += 1
            return removed

    def invite(self, email: str, name: str) -> str:
        if not self.key_file or not self.key_file.is_file():
            raise Problem(503, "invitations_not_configured")
        key = self.key_file.read_text().strip()
        if not key:
            raise Problem(503, "invitations_not_configured")
        try:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    self.url + "/invite",
                    params={"redirect_to": self.origin + "/?account=setup"},
                    headers={"apikey": key, "Authorization": "Bearer " + key},
                    json={"email": email, "data": {"full_name": name}},
                )
            if response.status_code not in (200, 201):
                raise Problem(502, "invitation_not_confirmed")
            result = response.json()
            if str(result.get("email", "")).casefold() != email.casefold():
                raise ValueError("Unexpected invited identity")
            return str(UUID(result["id"]))
        except (httpx.HTTPError, ValueError, KeyError):
            raise Problem(502, "invitation_not_confirmed") from None

    def reset_password(self, email: str) -> None:
        if not self.key_file or not self.key_file.is_file():
            raise Problem(503, "invitations_not_configured")
        key = self.key_file.read_text().strip()
        if not key:
            raise Problem(503, "invitations_not_configured")
        try:
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    self.url + "/recover",
                    params={"redirect_to": self.origin + "/?account=setup"},
                    headers={"apikey": key, "Authorization": "Bearer " + key},
                    json={"email": email},
                )
            if response.status_code == 429:
                raise Problem(429, "rate_limit_exceeded")
            if not response.is_success:
                raise Problem(502, "password_reset_not_confirmed")
        except httpx.HTTPError:
            raise Problem(502, "password_reset_not_confirmed") from None


class StaffVerifier(TokenVerifier):
    def __init__(self, local: Settings, provider: Settings):
        super().__init__(local)
        if provider.auth_provider != "supabase" or not provider.require_mfa:
            raise ValueError("Staff access requires Supabase MFA")
        self.provider = TokenVerifier(provider)
        self.provider_engine, self.provider_factory = database(provider)

    def provider_active(self, claims: dict[str, Any]) -> None:
        """The password sign-in's provider session must still be live (not signed out/revoked)."""
        try:
            subject, session_id = UUID(claims["sub"]), UUID(claims["session_id"])
        except (KeyError, ValueError, TypeError):
            raise Problem(401, "invalid_token") from None
        with self.provider_factory.begin() as remote:
            remote.execute(text("SET TRANSACTION READ ONLY"))
            remote.execute(
                text("SELECT set_config('request.jwt.claim.sub', :sub, true)"),
                {"sub": str(subject)},
            )
            if not remote.scalar(
                text("SELECT atm.provider_session_active(:id)"), {"id": session_id}
            ):
                raise Problem(401, "provider_session_revoked")

    def principal(self, session: Session, claims: dict[str, Any]) -> Principal:
        principal = session.scalar(
            select(Principal).where(
                Principal.issuer == self.provider.settings.issuer,
                Principal.subject == claims["sub"],
                Principal.team == "booth",
            )
        )
        if (
            not principal
            or not principal.active
            or claims["iat"] < principal.valid_after.timestamp()
            or principal.role not in ("admin", "analyst", "supervisor")
        ):
            raise Problem(401, "identity_inactive")
        return principal

    def from_claims(self, session: Session, claims: dict[str, Any], request_id: str) -> Actor:
        # Legacy presenter/synthetic sessions cannot cross this boundary.
        if claims.get("staff_session") is not True or claims.get("exp", 0) <= now().timestamp():
            raise Problem(401, "authentication_required")
        # Set only by the server after a successful face match; never taken from the browser.
        if claims.get("second_factor") != "face":
            raise Problem(403, "mfa_required")
        self.provider_active(claims)
        principal = self.principal(session, claims)
        return Actor(
            principal.id,
            principal.team,
            principal.role,
            datetime.fromtimestamp(claims["auth_time"], UTC),
            request_id,
        )


def install(app: FastAPI, provider: Settings, key_file: Path | None = None) -> None:
    if app.state.settings.environment != "booth":
        raise ValueError("Staff workspace requires the isolated local database")
    verifier = StaffVerifier(app.state.settings, provider)
    app.state.verifier = verifier
    app.state.staff_access = True
    app.state.invites = InviteProvider(provider.issuer, key_file, app.state.settings.browser_origin)

    def password_claims(request: Request) -> dict[str, Any]:
        """Step one: a same-origin request carrying the provider's password sign-in token."""
        if request.headers.get("origin") != app.state.settings.browser_origin:
            raise Problem(403, "origin_denied")
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise Problem(401, "bearer_required")
        claims = verifier.provider.decode(authorization[7:])
        safe = {
            k: claims[k]
            for k in ("iss", "sub", "iat", "auth_time", "aal", "session_id")
            if k in claims
        }
        verifier.provider_active(safe)
        return safe

    def check_face(principal: Principal, body: face.FaceInput, request_id: str) -> float | None:
        """Step two: enroll or match the face in its own transaction, so failures are counted.

        Returns the average match distance, or None when the face was enrolled just now.
        """
        actor = Actor(principal.id, principal.team, principal.role, now(), request_id)
        with app.state.factory.begin() as tx:
            row = tx.scalar(
                select(StaffFace).where(StaffFace.principal_id == principal.id).with_for_update()
            )
            if row is None:
                if not body.enroll:
                    raise Problem(403, "face_enrollment_required")
                if (
                    len(body.samples) < face.ENROLL_SAMPLES
                    or not face.distinct(body.samples)
                    or not face.consistent(body.samples)
                ):
                    raise Problem(422, "face_samples_invalid")
                tx.add(StaffFace(principal_id=principal.id, templates=body.samples))
                services.audit(tx, actor, "staff.face_enrolled", principal.id)
                return None
            if body.enroll:
                raise Problem(409, "face_already_enrolled")
            if row.locked_until and row.locked_until > now():
                raise Problem(429, "face_verification_locked")
            if not face.distinct(body.samples):
                raise Problem(422, "face_samples_invalid")
            result = face.match(row.templates, body.samples)
            if result is None:
                row.failures += 1
                if row.failures >= face.MAX_FAILURES:
                    row.failures = 0
                    row.locked_until = now() + timedelta(seconds=face.LOCK_SECONDS)
                    services.audit(tx, actor, "staff.face_locked", principal.id)
                services.audit(tx, actor, "staff.face_rejected", principal.id)
            else:
                row.failures, row.locked_until = 0, None
        # Raised after the commit so the failed attempt stays counted.
        if result is None:
            raise Problem(401, "face_not_recognized")
        return result

    @app.post("/api/v1/staff/face/status")
    def face_status(request: Request, s: DB) -> dict[str, bool]:
        claims = password_claims(request)
        principal = verifier.principal(s, claims)
        return {"enrolled": s.get(StaffFace, principal.id) is not None}

    @app.post("/api/v1/staff/session")
    def login(
        request: Request,
        response: Response,
        s: DB,
        payload: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> dict[str, Any]:
        safe = password_claims(request)
        principal = verifier.principal(s, safe)
        # Validate the face samples only after the password token is accepted.
        try:
            body = face.FaceInput.model_validate(payload)
        except ValidationError:
            raise Problem(422, "face_samples_invalid") from None
        distance = check_face(principal, body, request.state.request_id)
        safe.update(
            staff_session=True,
            exp=int(now().timestamp()) + 86400,
            second_factor="face",
            face_verified_at=int(now().timestamp()),
        )
        actor = verifier.from_claims(s, safe, request.state.request_id)
        previous = s.get(
            BrowserSession, sessions.digest(request.cookies.get(sessions.cookie_name(request), ""))
        )
        if previous:
            previous.revoked = True
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        deadline = datetime.fromtimestamp(safe["exp"], UTC)
        s.add(
            BrowserSession(
                token_hash=sessions.digest(token),
                csrf_hash=sessions.digest(csrf),
                principal_id=actor.id,
                claims=safe,
                expires_at=deadline,
                idle_expires_at=deadline,
            )
        )
        response.set_cookie(
            sessions.cookie_name(request),
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/api/v1",
            max_age=86400,
        )
        services.audit(s, actor, "staff.signed_in", actor.id)
        return {
            "csrf_token": csrf,
            "face": {
                "enrolled_now": distance is None,
                "distance": None if distance is None else round(distance, 3),
            },
        }

    @app.get("/api/v1/staff/users")
    def users(s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        rows = s.execute(
            select(StaffProfile, Principal.active, StaffFace.principal_id)
            .outerjoin(Principal, Principal.id == StaffProfile.principal_id)
            .outerjoin(StaffFace, StaffFace.principal_id == StaffProfile.principal_id)
            .where(StaffProfile.status != "DELETED")
            .order_by(StaffProfile.email)
        ).all()
        return {
            "items": [
                {
                    "id": r.id,
                    "name": r.name,
                    "email": r.email,
                    "role": r.role,
                    "status": r.status,
                    "active": active,
                    "version": r.version,
                    "is_self": r.principal_id == a.id,
                    "face_enrolled": enrolled is not None,
                }
                for r, active, enrolled in rows
            ]
        }

    @app.post("/api/v1/staff/users", status_code=201)
    def invite(body: InviteInput, request: Request, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        email = body.email.strip().casefold()
        restored = False
        with app.state.factory.begin() as intent:
            row = intent.scalar(
                select(StaffProfile).where(StaffProfile.email == email).with_for_update()
            )
            if row and row.status != "DELETED":
                raise Problem(409, "user_already_registered")
            if row:
                row.name, row.role = body.name.strip(), body.role
                row.version += 1
                if row.principal_id:
                    principal = intent.scalar(
                        select(Principal).where(Principal.id == row.principal_id).with_for_update()
                    )
                    if not principal or principal.team != a.team:
                        raise Problem(404, "not_found")
                    principal.role, principal.active, principal.valid_after = body.role, True, now()
                    intent.execute(
                        update(BrowserSession)
                        .where(BrowserSession.principal_id == principal.id)
                        .values(revoked=True)
                    )
                    row.status = "ACTIVE"
                    services.audit(intent, a, "staff.restored", principal.id)
                    restored = True
                else:
                    row.status = "PENDING"
            else:
                row = StaffProfile(email=email, name=body.name.strip(), role=body.role)
                intent.add(row)
            intent.flush()
            user_id = row.id
            if not restored:
                services.audit(intent, a, "staff.invitation_requested", row.id)
        if restored:
            send_reset(user_id, a)
            return {"id": user_id, "status": "RESTORED_RESET_SENT"}
        return deliver(user_id, a)

    def deliver(user_id: UUID, a: Actor) -> dict[str, Any]:
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        with app.state.factory.begin() as prepare:
            row = prepare.scalar(
                select(StaffProfile).where(StaffProfile.id == user_id).with_for_update()
            )
            if not row or row.status == "DELETED":
                raise Problem(404, "not_found")
            if row.principal_id is not None:
                raise Problem(409, "user_already_registered")
            if row.attempted_at and (now() - row.attempted_at).total_seconds() < 60:
                raise Problem(429, "rate_limit_exceeded")
            row.attempted_at = now()
            email, name = row.email, row.name
        # Persist intent before email; never automatically repeat an uncertain send.
        subject = app.state.invites.invite(email, name)
        with app.state.factory.begin() as finish:
            current = finish.scalar(select(Principal).where(Principal.id == a.id).with_for_update())
            if (
                not current
                or not current.active
                or current.role != "admin"
                or current.team != a.team
                or current.valid_after > a.auth_time
            ):
                raise Problem(403, "permission_denied")
            saved = finish.scalar(
                select(StaffProfile).where(StaffProfile.id == user_id).with_for_update()
            )
            if saved is None or saved.status == "DELETED":
                raise Problem(404, "not_found")
            principal = finish.scalar(
                select(Principal).where(
                    Principal.issuer == provider.issuer, Principal.subject == subject
                )
            )
            if principal is not None and principal.id != saved.principal_id:
                raise Problem(409, "identity_already_registered")
            if saved.principal_id and (principal is None or principal.id != saved.principal_id):
                raise Problem(409, "identity_already_registered")
            if principal is None:
                principal = Principal(
                    issuer=provider.issuer,
                    subject=subject,
                    role=saved.role,
                    team="booth",
                    valid_after=now() - timedelta(seconds=1),
                )
                finish.add(principal)
                finish.flush()
                saved.principal_id = principal.id
            saved.status = "INVITED"
            services.audit(finish, a, "staff.invited", principal.id)
        return {"id": user_id, "status": "INVITED"}

    @app.post("/api/v1/staff/users/{user_id}/invite")
    def resend(user_id: UUID, a: Identity) -> dict[str, Any]:
        return deliver(user_id, a)

    @app.post("/api/v1/staff/users/{user_id}/reset-password")
    def send_reset(user_id: UUID, a: Identity) -> dict[str, str]:
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        with app.state.factory.begin() as prepare:
            row = prepare.scalar(
                select(StaffProfile).where(StaffProfile.id == user_id).with_for_update()
            )
            if not row or row.status == "DELETED" or not row.principal_id:
                raise Problem(404, "not_found")
            if row.principal_id == a.id:
                raise Problem(409, "cannot_reset_own_account")
            principal = prepare.get(Principal, row.principal_id)
            if not principal or not principal.active or principal.team != a.team:
                raise Problem(404, "not_found")
            if row.attempted_at and (now() - row.attempted_at).total_seconds() < 60:
                raise Problem(429, "rate_limit_exceeded")
            row.attempted_at = now()
            email = row.email
            services.audit(prepare, a, "staff.password_reset_requested", principal.id)
        app.state.invites.reset_password(email)
        with app.state.factory.begin() as recorded:
            services.audit(recorded, a, "staff.password_reset_submitted", user_id)
        return {"status": "RESET_SENT"}

    @app.patch("/api/v1/staff/users/{user_id}")
    def change(user_id: UUID, body: StaffUpdate, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        row = s.scalar(select(StaffProfile).where(StaffProfile.id == user_id).with_for_update())
        if not row or row.status == "DELETED" or not row.principal_id:
            raise Problem(404, "not_found")
        services.version(row.version, body.expected_version)
        principal = s.get(Principal, row.principal_id)
        if not principal or principal.team != a.team:
            raise Problem(404, "not_found")
        # Prevent self-lockout; another administrator must change this account.
        if principal.id == a.id:
            raise Problem(409, "cannot_change_own_access")
        principal.role, principal.active, principal.valid_after = body.role, body.active, now()
        row.role, row.version = body.role, row.version + 1
        s.execute(
            update(BrowserSession)
            .where(BrowserSession.principal_id == principal.id)
            .values(revoked=True)
        )
        services.audit(s, a, "staff.access_changed", principal.id)
        return {"version": row.version}

    @app.delete("/api/v1/staff/users/{user_id}", status_code=204)
    def delete_user(user_id: UUID, body: dto.VersionInput, s: DB, a: Identity) -> None:
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        row = s.scalar(select(StaffProfile).where(StaffProfile.id == user_id).with_for_update())
        if not row or row.status == "DELETED":
            raise Problem(404, "not_found")
        services.version(row.version, body.expected_version)
        if row.principal_id == a.id:
            raise Problem(409, "cannot_change_own_access")
        if row.principal_id:
            principal = s.scalar(
                select(Principal).where(Principal.id == row.principal_id).with_for_update()
            )
            if not principal or principal.team != a.team:
                raise Problem(404, "not_found")
            principal.active, principal.valid_after = False, now()
            s.execute(
                update(BrowserSession)
                .where(BrowserSession.principal_id == principal.id)
                .values(revoked=True)
            )
            # Biometric data is not retained for deleted users; a restored user enrolls again.
            s.execute(delete(StaffFace).where(StaffFace.principal_id == principal.id))
        row.status, row.version = "DELETED", row.version + 1
        services.audit(s, a, "staff.deleted", row.principal_id or row.id)

    @app.delete("/api/v1/staff/users/{user_id}/face", status_code=204)
    def reset_face(user_id: UUID, s: DB, a: Identity) -> None:
        """Remove a user's face enrollment; they enroll again at their next sign-in."""
        a.require("admin")
        if (now() - a.auth_time).total_seconds() > STAFF_ACTION_AUTH_SECONDS:
            raise Problem(403, "fresh_authentication_required")
        row = s.scalar(select(StaffProfile).where(StaffProfile.id == user_id).with_for_update())
        if not row or row.status == "DELETED" or not row.principal_id:
            raise Problem(404, "not_found")
        # Resetting your own face would let a password alone enroll a new one.
        if row.principal_id == a.id:
            raise Problem(409, "cannot_change_own_access")
        principal = s.get(Principal, row.principal_id)
        if not principal or principal.team != a.team:
            raise Problem(404, "not_found")
        enrolled = s.scalar(
            select(StaffFace).where(StaffFace.principal_id == principal.id).with_for_update()
        )
        if enrolled is None:
            raise Problem(409, "face_not_enrolled")
        s.delete(enrolled)
        s.execute(
            update(BrowserSession)
            .where(BrowserSession.principal_id == principal.id)
            .values(revoked=True)
        )
        services.audit(s, a, "staff.face_reset", principal.id)
