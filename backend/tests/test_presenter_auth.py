"""Presenter MFA gates synthetic roles without changing real account permissions."""

from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from atm_backend import booth, presenter_auth
from atm_backend.models import BrowserSession, Principal, now


def test_presenter_login_role_switch_expiry_and_revocation(env, monkeypatch):
    app, cfg = env["app"], env["settings"]
    provider_cfg = cfg.model_copy(update={"auth_provider": "supabase"})
    original_verifier = app.state.verifier
    # Provider identity checks use the isolated test database, never real accounts.
    monkeypatch.setattr(presenter_auth, "database", lambda _: (app.state.engine, env["runtime"]))
    cfg.environment = "booth"
    cfg.issuer = "https://booth.local"
    cfg.browser_origin = "https://localhost:8555"
    cfg.require_mfa = False
    original_verifier.settings = provider_cfg.model_copy(
        update={"auth_provider": "oidc", "require_mfa": False}
    )
    booth.register(app)
    presenter_auth.install(app, provider_cfg)
    verifier = app.state.verifier
    # Provider session revocation has separate Supabase-boundary tests. This test
    # exercises real signature checks, principal revocation and local session lifecycle.
    monkeypatch.setattr(verifier.provider, "from_claims", original_verifier.from_claims)
    monkeypatch.setattr(verifier.provider, "authorize_claims", original_verifier.authorize_claims)
    with env["factory"].begin() as s:
        for role in ("analyst", "supervisor", "admin", "ingestion"):
            s.add(
                Principal(
                    issuer=cfg.issuer,
                    subject="booth-" + role,
                    role=role,
                    team="booth",
                    valid_after=now() - timedelta(minutes=1),
                )
            )

    def credential(name="supervisor", **changes):
        # env.headers reads cfg, so explicitly use the trusted provider issuer.
        return env["headers"](
            name,
            iss=provider_cfg.issuer,
            aal="aal2",
            role="authenticated",
            is_anonymous=False,
            session_id=str(uuid4()),
            amr=[{"method": "totp", "timestamp": int(now().timestamp())}],
            **changes,
        )

    with TestClient(app, base_url=cfg.browser_origin) as client:
        client.headers["Origin"] = cfg.browser_origin
        app.state.booth_bootstrap = True
        legacy = client.post("/api/v1/booth/role", json={"role": "admin"})
        app.state.booth_bootstrap = False
        assert legacy.status_code == 200
        client.headers["X-CSRF-Token"] = legacy.json()["csrf_token"]
        assert client.get("/api/v1/me").status_code == 401
        assert client.post("/api/v1/booth/role", json={"role": "admin"}).status_code == 401
        client.cookies.clear()
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/v1/booth/role", json={"role": "admin"}).status_code == 401
        assert client.post("/api/v1/booth/session").status_code == 401
        assert (
            client.post(
                "/api/v1/booth/session", headers={"Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )
        denied = credential("analyst")
        assert client.post("/api/v1/booth/session", headers=denied).status_code == 403
        no_mfa = credential()
        claims = verifier.provider.decode(no_mfa["Authorization"][7:])
        claims["aal"] = "aal1"

        # Decode-only mock targets a valid password-only token; identity remains checked.
        with monkeypatch.context() as patch:
            patch.setattr(verifier.provider, "decode", lambda _: claims)
            assert client.post("/api/v1/booth/session", headers=no_mfa).status_code == 403
        wrong_origin = {**credential(), "Origin": "https://other.example"}
        assert client.post("/api/v1/booth/session", headers=wrong_origin).status_code == 403
        result = client.post("/api/v1/booth/session", headers=credential())
        assert result.status_code == 200, result.text
        assert result.json()["identity"]["role"] == "analyst"
        csrf = result.json()["csrf_token"]
        assert (
            "HttpOnly" in result.headers["set-cookie"] and "Secure" in result.headers["set-cookie"]
        )
        assert client.post("/api/v1/booth/role", json={"role": "admin"}).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        result = client.post("/api/v1/booth/role", json={"role": "admin"})
        assert result.status_code == 200
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        assert client.get("/api/v1/me").json()["role"] == "admin"
        with env["factory"].begin() as s:
            rows = [row for row in s.scalars(select(BrowserSession)) if row.claims.get("presenter")]
            assert sum(not row.revoked for row in rows) == 1
            current = next(row for row in rows if not row.revoked)
            assert current.claims["presenter"]["sub"] == "supervisor"
            assert current.claims["exp"] == current.claims["presenter"]["workspace_expires_at"]
            assert 86390 <= (current.expires_at - now()).total_seconds() <= 86400
            assert current.idle_expires_at == current.expires_at
            deadline = current.expires_at
            # A short-lived provider JWT expiring must not end a verified local session.
            current.claims = {
                **current.claims,
                "presenter": {
                    **current.claims["presenter"],
                    "exp": int(now().timestamp()) - 1,
                },
            }
        assert client.get("/api/v1/me").status_code == 200
        switched = client.post("/api/v1/booth/role", json={"role": "supervisor"})
        assert switched.status_code == 200
        client.headers["X-CSRF-Token"] = switched.json()["csrf_token"]
        with env["factory"].begin() as s:
            current = s.scalar(
                select(BrowserSession)
                .where(
                    BrowserSession.revoked.is_(False),
                    BrowserSession.claims["presenter"].is_not(None),
                )
                .order_by(BrowserSession.expires_at.desc())
            )
            assert current.expires_at == deadline
            assert current.idle_expires_at == deadline
            current.expires_at = now() - timedelta(seconds=1)
        assert client.get("/api/v1/me").status_code == 401
        result = client.post("/api/v1/booth/session", headers=credential())
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.post("/api/v1/booth/role", json={"role": "analyst"}).status_code == 401
        result = client.post("/api/v1/booth/session", headers=credential())
        assert result.status_code == 200
        with env["factory"].begin() as s:
            s.get(Principal, env["ids"]["supervisor"]).active = False
        assert client.get("/api/v1/me").status_code == 401
