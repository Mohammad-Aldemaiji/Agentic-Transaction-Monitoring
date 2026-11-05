from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from atm_backend import booth, staff_access
from atm_backend.models import BrowserSession, Principal, now
from atm_backend.security import Problem


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
                aal="aal2",
                iat=int(now().timestamp()),
                auth_time=int(now().timestamp()),
                exp=int(now().timestamp()) + 900,
                session_id=str(uuid4()),
            )

    def decode(token):
        if token not in claims:
            raise Problem(401, "invalid_token")
        return dict(claims[token])

    def active(values):
        if values.get("aal") != "aal2":
            raise Problem(403, "mfa_required")

    monkeypatch.setattr(app.state.verifier.provider, "decode", decode)
    monkeypatch.setattr(app.state.verifier, "provider_active", active)
    with TestClient(app, base_url=cfg.browser_origin) as client:
        client.headers["Origin"] = cfg.browser_origin

        def login(role):
            result = client.post(
                "/api/v1/staff/session", headers={"Authorization": "Bearer " + role}
            )
            if result.status_code == 200:
                client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
            return result

        try:
            yield env, client, login, claims, identities
        finally:
            staff_access.StaffBase.metadata.drop_all(migration_engine)


def test_staff_boundaries_and_revocation(staff):
    env, client, login, claims, identities = staff
    assert client.post("/api/v1/staff/session").status_code == 401
    claims["analyst"]["aal"] = "aal1"
    assert login("analyst").status_code == 403
    claims["analyst"]["aal"] = "aal2"
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
