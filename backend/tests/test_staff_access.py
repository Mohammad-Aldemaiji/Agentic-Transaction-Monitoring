import itertools
import random
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from atm_backend import booth, face_verification, staff_access
from atm_backend.models import Audit, BrowserSession, Principal, now
from atm_backend.security import Problem

FACES: dict[str, list[float]] = {}


def person(name):
    """A stable synthetic 128-number face descriptor per name (no real biometric data)."""
    if name not in FACES:
        rng = random.Random(name)
        vector = [rng.gauss(0, 1) for _ in range(128)]
        norm = sum(x * x for x in vector) ** 0.5
        FACES[name] = [x / norm for x in vector]  # unit length, like a real descriptor
    return FACES[name]


def scan(name, count=3, jitter=0.012, seed=0):
    """Fresh camera frames of the same face: small per-frame noise, never identical."""
    rng = random.Random(f"{name}-{seed}")
    return [[x + rng.gauss(0, jitter) for x in person(name)] for _ in range(count)]


@pytest.fixture
def staff(env, monkeypatch, migration_engine):
    app, cfg = env["app"], env["settings"]
    provider = cfg.model_copy(
        update={"auth_provider": "supabase", "issuer": "https://test.example/auth/v1"}
    )
    cfg.environment, cfg.issuer, cfg.browser_origin = (
        "booth",
        "https://booth.local",
        "https://localhost:8555",
    )
    app.state.factory = env["factory"]  # isolated owner mirrors local workspace's ACLs
    monkeypatch.setattr(staff_access, "database", lambda _: (migration_engine, env["factory"]))
    staff_access.StaffBase.metadata.create_all(migration_engine)
    booth.register(app)
    staff_access.install(app, provider)
    claims, identities = {}, {}
    with env["factory"].begin() as session:
        for role in ("admin", "analyst", "supervisor"):
            subject = str(uuid4())
            principal = Principal(
                issuer=provider.issuer,
                subject=subject,
                team="booth",
                role=role,
                valid_after=now() - timedelta(minutes=1),
            )
            session.add(principal)
            session.flush()
            identities[role] = principal.id
            session.add(
                staff_access.StaffProfile(
                    email=role + "@example.com",
                    name=role,
                    role=role,
                    principal_id=principal.id,
                    status="ENABLED",
                )
            )
            claims[role] = dict(
                sub=subject,
                iss=provider.issuer,
                aal="aal1",
                iat=int(now().timestamp()),
                auth_time=int(now().timestamp()),
                exp=int(now().timestamp()) + 900,
                session_id=str(uuid4()),
            )

    def decode(token):
        if token not in claims:
            raise Problem(401, "invalid_token")
        return dict(claims[token])

    revoked = set()

    def active(values):
        if values["session_id"] in revoked:
            raise Problem(401, "provider_session_revoked")

    monkeypatch.setattr(app.state.verifier.provider, "decode", decode)
    monkeypatch.setattr(app.state.verifier, "provider_active", active)
    with TestClient(app, base_url=cfg.browser_origin) as client:
        client.headers["Origin"] = cfg.browser_origin

        enrolled = set()
        attempts = itertools.count()

        def login(role, samples=None, enroll=None):
            """Password token, then face: enrolls each role's face on its first sign-in."""
            first = role not in enrolled if enroll is None else enroll
            body = {
                "samples": samples
                if samples is not None
                else scan(role, 5 if first else 3, seed=next(attempts)),
                "enroll": first,
            }
            result = client.post(
                "/api/v1/staff/session", headers={"Authorization": "Bearer " + role}, json=body
            )
            if result.status_code == 200:
                enrolled.add(role)
                client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
            return result

        client.revoked = revoked
        try:
            yield env, client, login, claims, identities
        finally:
            staff_access.StaffBase.metadata.drop_all(migration_engine)


def test_staff_boundaries_and_revocation(staff):
    env, client, login, claims, identities = staff
    assert client.post("/api/v1/staff/session").status_code == 401
    assert login("analyst").status_code == 200
    assert client.get("/api/v1/me").json()["role"] == "analyst"
    assert client.post("/api/v1/booth/role", json={"role": "admin"}).status_code == 404
    assert client.get("/api/v1/staff/users").status_code == 403
    assert (
        client.post(
            "/api/v1/staff/users",
            json={"name": "Someone", "email": "new@example.com", "role": "admin"},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/booth/feed", json={"count": 10}).status_code == 403
    old_cookie = client.cookies.get("atm_booth_session")
    assert login("admin").status_code == 200
    users = client.get("/api/v1/staff/users").json()["items"]
    analyst = next(u for u in users if u["role"] == "analyst")
    admin = next(u for u in users if u["role"] == "admin")
    body = {"role": "supervisor", "active": True, "expected_version": 1}
    assert client.patch("/api/v1/staff/users/" + admin["id"], json=body).status_code == 409
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.patch("/api/v1/staff/users/" + analyst["id"], json=body).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.patch("/api/v1/staff/users/" + analyst["id"], json=body).status_code == 200
    assert client.patch("/api/v1/staff/users/" + analyst["id"], json=body).status_code == 409
    client.cookies.clear()
    client.cookies.set("atm_booth_session", old_cookie)
    assert client.get("/api/v1/me").status_code == 401
    with env["factory"].begin() as session:
        legacy = BrowserSession(
            token_hash="legacy",
            csrf_hash="csrf",
            principal_id=identities["admin"],
            claims={"presenter": claims["admin"], "exp": int(now().timestamp()) + 900},
            expires_at=now() + timedelta(hours=1),
            idle_expires_at=now() + timedelta(hours=1),
        )
        with pytest.raises(Problem):
            env["app"].state.verifier.from_claims(session, legacy.claims, "test")


def test_invitation_failure_retry_and_no_password(staff, monkeypatch):
    env, client, login, claims, identities = staff
    login("admin")
    body = {"name": "New analyst", "email": "new@example.com", "role": "analyst"}

    def unavailable(email, name):
        raise Problem(502, "invitation_not_confirmed")

    monkeypatch.setattr(env["app"].state.invites, "invite", unavailable)
    assert client.post("/api/v1/staff/users", json=body).status_code == 502
    rows = client.get("/api/v1/staff/users").json()["items"]
    pending = next(u for u in rows if u["email"] == body["email"])
    assert pending["active"] is None
    assert client.post("/api/v1/staff/users", json=body).status_code == 409
    assert client.post("/api/v1/staff/users/" + pending["id"] + "/invite").status_code == 429
    with env["factory"].begin() as session:
        row = session.scalar(
            select(staff_access.StaffProfile).where(
                staff_access.StaffProfile.email == body["email"]
            )
        )
        row.attempted_at = now() - timedelta(minutes=2)
    subject = str(uuid4())
    monkeypatch.setattr(env["app"].state.invites, "invite", lambda email, name: subject)
    assert client.post("/api/v1/staff/users/" + pending["id"] + "/invite").status_code == 200
    with env["factory"].begin() as session:
        principal = session.scalar(select(Principal).where(Principal.subject == subject))
        assert principal.role == "analyst"
    assert (
        client.post(
            "/api/v1/staff/users",
            json={**body, "email": "other@example.com", "password": "not-accepted"},
        ).status_code
        == 422
    )


def test_delete_staff_revokes_access_and_preserves_history(staff, monkeypatch):
    env, client, login, claims, identities = staff
    login("admin")
    users = client.get("/api/v1/staff/users").json()["items"]
    analyst = next(u for u in users if u["role"] == "analyst")
    admin = next(u for u in users if u["role"] == "admin")

    def remove(user, version=1):
        return client.request(
            "DELETE",
            "/api/v1/staff/users/" + user["id"],
            json={"expected_version": version},
        )

    login("analyst")
    old_cookie = client.cookies.get("atm_booth_session")
    assert remove(admin).status_code == 403
    login("admin")
    assert remove(admin).status_code == 409
    assert remove(analyst, 99).status_code == 409
    csrf = client.headers.pop("X-CSRF-Token")
    assert remove(analyst).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert remove(analyst).status_code == 204
    assert analyst["id"] not in {u["id"] for u in client.get("/api/v1/staff/users").json()["items"]}
    assert remove(analyst).status_code == 404
    assert client.post("/api/v1/staff/users/" + analyst["id"] + "/invite").status_code == 404
    assert (
        client.patch(
            "/api/v1/staff/users/" + analyst["id"],
            json={
                "expected_version": 2,
                "role": "analyst",
                "active": True,
            },
        ).status_code
        == 404
    )
    with env["factory"].begin() as session:
        principal = session.get(Principal, identities["analyst"])
        assert principal is not None and principal.active is False
    client.cookies.clear()
    client.cookies.set("atm_booth_session", old_cookie)
    assert client.get("/api/v1/me").status_code == 401
    assert login("analyst").status_code == 401
    client.cookies.clear()
    assert login("admin").status_code == 200
    sent = []
    monkeypatch.setattr(env["app"].state.invites, "reset_password", sent.append)
    restored = client.post(
        "/api/v1/staff/users",
        json={
            "email": analyst["email"].upper(),
            "name": "Restored analyst",
            "role": "supervisor",
        },
    )
    assert restored.status_code == 201
    assert restored.json() == {"id": analyst["id"], "status": "RESTORED_RESET_SENT"}
    assert sent == [analyst["email"]]
    assert (
        client.post("/api/v1/staff/users/" + analyst["id"] + "/reset-password").status_code == 429
    )
    rows = client.get("/api/v1/staff/users").json()["items"]
    restored_user = next(u for u in rows if u["id"] == analyst["id"])
    assert restored_user["role"] == "supervisor"
    assert restored_user["active"] is True
    assert (
        client.post(
            "/api/v1/staff/users",
            json={
                "email": analyst["email"],
                "name": "Duplicate",
                "role": "analyst",
            },
        ).status_code
        == 409
    )
    with env["factory"].begin() as session:
        principal = session.get(Principal, identities["analyst"])
        assert principal.active and principal.role == "supervisor"
    client.cookies.clear()
    client.cookies.set("atm_booth_session", old_cookie)
    assert client.get("/api/v1/me").status_code == 401


def test_staff_management_requires_authentication_within_five_hours(staff):
    env, client, login, claims, identities = staff
    claims["admin"]["auth_time"] = int((now() - timedelta(hours=4)).timestamp())
    assert login("admin").status_code == 200
    users = client.get("/api/v1/staff/users").json()["items"]
    analyst = next(u for u in users if u["role"] == "analyst")
    url = "/api/v1/staff/users/" + analyst["id"]
    body = {"expected_version": 1, "role": "supervisor", "active": True}
    assert client.patch(url, json=body).status_code == 200
    claims["admin"]["auth_time"] = int((now() - timedelta(hours=5, seconds=1)).timestamp())
    assert login("admin").status_code == 200
    body["expected_version"] = 2
    result = client.patch(url, json=body)
    assert result.status_code == 403
    assert "fresh_authentication_required" in result.text


def test_admin_cannot_send_self_reset(staff, monkeypatch):
    env, client, login, claims, identities = staff
    login("admin")
    sent = []
    monkeypatch.setattr(env["app"].state.invites, "reset_password", sent.append)
    users = client.get("/api/v1/staff/users").json()["items"]
    admin = next(u for u in users if u["role"] == "admin")
    analyst = next(u for u in users if u["role"] == "analyst")
    assert admin["is_self"] is True
    assert analyst["is_self"] is False
    response = client.post("/api/v1/staff/users/" + admin["id"] + "/reset-password")
    assert response.status_code == 409
    assert "cannot_reset_own_account" in response.text
    assert sent == []
    assert (
        client.post("/api/v1/staff/users/" + analyst["id"] + "/reset-password").status_code == 200
    )
    assert sent == [analyst["email"]]


def face_post(client, role, samples, enroll=False):
    return client.post(
        "/api/v1/staff/session",
        headers={"Authorization": "Bearer " + role},
        json={"samples": samples, "enroll": enroll},
    )


def test_face_replaces_authenticator_and_is_checked_by_the_server(staff):
    env, client, login, claims, identities = staff
    status = "/api/v1/staff/face/status"
    bearer = {"Authorization": "Bearer analyst"}
    # The password token alone (aal1, no authenticator) is accepted for step one only.
    assert client.post(status).status_code == 401
    assert (
        client.post(status, headers={**bearer, "Origin": "https://evil.example"}).status_code == 403
    )
    assert client.post(status, headers=bearer).json() == {"enrolled": False}
    assert client.get("/api/v1/me").status_code == 401
    # No face sample, no session; samples are validated only after the token.
    assert client.post("/api/v1/staff/session", headers=bearer).status_code == 422
    assert face_post(client, "nobody", scan("analyst"), True).status_code == 401
    # Enrollment needs five consistent, distinct frames of one person.
    assert face_post(client, "analyst", scan("analyst"), True).status_code == 422
    frozen = [person("analyst")] * 5
    assert face_post(client, "analyst", frozen, True).status_code == 422
    mixed = scan("analyst", 4) + scan("stranger", 1)
    assert face_post(client, "analyst", mixed, True).status_code == 422
    assert face_post(client, "analyst", scan("analyst"), False).status_code == 403
    assert face_post(client, "analyst", [[0.0] * 128] * 3).status_code == 422
    assert face_post(client, "analyst", [[2.0] * 128] * 3).status_code == 422
    assert face_post(client, "analyst", [[0.1] * 127] * 3).status_code == 422
    assert client.post(status, headers=bearer).json() == {"enrolled": False}
    enrolled = face_post(client, "analyst", scan("analyst", 5, seed=1), True)
    assert enrolled.status_code == 200
    assert enrolled.json()["face"] == {"enrolled_now": True, "distance": None}
    assert client.get("/api/v1/me").json()["role"] == "analyst"
    assert client.post(status, headers=bearer).json() == {"enrolled": True}
    # A second enrollment cannot overwrite the face with a password alone.
    assert face_post(client, "analyst", scan("stranger", 5), True).status_code == 409
    client.cookies.clear()
    # Same person on a later scan: accepted, with the match distance reported.
    verified = face_post(client, "analyst", scan("analyst", 3, seed=2))
    assert verified.status_code == 200
    assert 0 < verified.json()["face"]["distance"] < face_verification.MATCH_THRESHOLD
    session_claims = None
    with env["factory"].begin() as session:
        row = session.scalars(
            select(BrowserSession).where(BrowserSession.principal_id == identities["analyst"])
        ).all()[-1]
        session_claims = dict(row.claims)
    assert session_claims["second_factor"] == "face"
    # A stored session without the server-set face claim is not accepted.
    session_claims.pop("second_factor")
    with env["factory"].begin() as session, pytest.raises(Problem) as missing:
        env["app"].state.verifier.from_claims(session, session_claims, "test")
    assert missing.value.code == "mfa_required"
    # A different face, or one bad frame among good ones, is rejected.
    client.cookies.clear()
    assert face_post(client, "analyst", scan("stranger")).status_code == 401
    assert (
        face_post(client, "analyst", scan("analyst", 2, seed=3) + scan("stranger", 1)).status_code
        == 401
    )
    # Replaying the stored template or one frame three times is not a live scan.
    with env["factory"].begin() as session:
        stored = session.get(staff_access.StaffFace, identities["analyst"]).templates
    assert face_post(client, "analyst", [stored[0]] + scan("analyst", 2, seed=4)).status_code == 401
    assert face_post(client, "analyst", [scan("analyst", 1, seed=5)[0]] * 3).status_code == 422
    assert client.get("/api/v1/me").status_code == 401
    with env["factory"].begin() as session:
        kinds = session.scalars(select(Audit.kind).where(Audit.kind.like("staff.face_%"))).all()
    assert kinds.count("staff.face_enrolled") == 1
    assert kinds.count("staff.face_rejected") == 3


def test_repeated_face_failures_lock_temporarily(staff):
    env, client, login, claims, identities = staff
    assert login("analyst").status_code == 200
    client.cookies.clear()
    for _ in range(face_verification.MAX_FAILURES - 1):
        assert face_post(client, "analyst", scan("stranger")).status_code == 401
    # A success resets the count of consecutive failures.
    assert face_post(client, "analyst", scan("analyst", seed=9)).status_code == 200
    client.cookies.clear()
    for _ in range(face_verification.MAX_FAILURES):
        assert face_post(client, "analyst", scan("stranger")).status_code == 401
    locked = face_post(client, "analyst", scan("analyst", seed=10))
    assert locked.status_code == 429
    assert "face_verification_locked" in locked.text
    with env["factory"].begin() as session:
        row = session.get(staff_access.StaffFace, identities["analyst"])
        row.locked_until = now() - timedelta(seconds=1)
    assert face_post(client, "analyst", scan("analyst", seed=11)).status_code == 200


def test_admin_face_reset_and_deletion_remove_biometric_data(staff):
    env, client, login, claims, identities = staff
    assert login("analyst").status_code == 200
    assert login("supervisor").status_code == 200
    supervisor_cookie = client.cookies.get("atm_booth_session")
    assert login("admin").status_code == 200
    users = {u["role"]: u for u in client.get("/api/v1/staff/users").json()["items"]}
    assert users["analyst"]["face_enrolled"] is True
    assert users["admin"]["face_enrolled"] is True
    # Admins cannot reset their own face (a password alone would then enroll a new one).
    own = client.delete("/api/v1/staff/users/" + users["admin"]["id"] + "/face")
    assert own.status_code == 409
    login("analyst")
    assert (
        client.delete("/api/v1/staff/users/" + users["supervisor"]["id"] + "/face").status_code
        == 403
    )
    login("admin")
    assert (
        client.delete("/api/v1/staff/users/" + users["supervisor"]["id"] + "/face").status_code
        == 204
    )
    assert (
        client.delete("/api/v1/staff/users/" + users["supervisor"]["id"] + "/face").status_code
        == 409
    )
    with env["factory"].begin() as session:
        assert session.get(staff_access.StaffFace, identities["supervisor"]) is None
    rows = {u["role"]: u for u in client.get("/api/v1/staff/users").json()["items"]}
    assert rows["supervisor"]["face_enrolled"] is False
    # The reset revokes the user's sessions; their next sign-in enrolls a new face.
    admin_cookie = client.cookies.get("atm_booth_session")
    client.cookies.clear()
    client.cookies.set("atm_booth_session", supervisor_cookie)
    assert client.get("/api/v1/me").status_code == 401
    assert face_post(client, "supervisor", scan("supervisor", seed=20)).status_code == 403
    assert face_post(client, "supervisor", scan("supervisor", 5, seed=21), True).status_code == 200
    client.cookies.clear()
    client.cookies.set("atm_booth_session", admin_cookie)
    response = client.request(
        "DELETE", "/api/v1/staff/users/" + users["analyst"]["id"], json={"expected_version": 1}
    )
    assert response.status_code == 204
    with env["factory"].begin() as session:
        assert session.get(staff_access.StaffFace, identities["analyst"]) is None


def test_revoked_provider_session_ends_face_sessions(staff):
    env, client, login, claims, identities = staff
    assert login("analyst").status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    client.revoked.add(claims["analyst"]["session_id"])
    assert client.get("/api/v1/me").status_code == 401
    assert (
        client.post(
            "/api/v1/staff/face/status", headers={"Authorization": "Bearer analyst"}
        ).status_code
        == 401
    )


def test_booth_reviewers_receive_live_traffic_numbers(staff):
    env, client, login, claims, identities = staff
    assert login("analyst").status_code == 200
    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    traffic = response.json()["transactions"]
    assert traffic["received"] == 0 and traffic["flag_rate"] is None
    assert traffic["average_risk_score"] is None


def test_operator_removes_only_old_authenticator_factors(tmp_path):
    import httpx

    key = tmp_path / "invite-key"
    key.write_text("service-key\n")
    provider = staff_access.InviteProvider(
        "https://test.example/auth/v1", key, "https://localhost:8555"
    )
    user, totp, phone = uuid4(), uuid4(), uuid4()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["apikey"] == "service-key"
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": str(totp), "factor_type": "totp", "status": "verified"},
                    {"id": str(phone), "factor_type": "phone", "status": "verified"},
                ],
            )
        return httpx.Response(200, json={"id": str(totp)})

    provider.transport = httpx.MockTransport(handler)
    assert provider.remove_authenticators(user) == 1
    base = f"/auth/v1/admin/users/{user}/factors"
    assert calls == [("GET", base), ("DELETE", f"{base}/{totp}")]
    key.write_text("")
    with pytest.raises(Problem):
        provider.remove_authenticators(user)
