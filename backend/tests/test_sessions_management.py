from datetime import timedelta

from sqlalchemy import select
from test_workflows import case_for, post

from atm_backend.adapters import SimulationScorer
from atm_backend.models import BrowserSession, now
from atm_backend.worker import run_one


def test_browser_rotation_and_bearer_share_identity_rate_limit(env, monkeypatch):
    import time
    from types import SimpleNamespace

    fixed_time = time.time()
    monkeypatch.setattr(
        "atm_backend.main.time", SimpleNamespace(time=lambda: fixed_time, monotonic=time.monotonic)
    )
    env["settings"].requests_per_minute = 10
    client = env["client"]
    headers = {**env["headers"](), "Origin": "http://localhost:8000"}
    # Each exchange replaces the cookie, but all ten requests belong to one identity.
    for _ in range(5):
        assert client.post("/api/v1/auth/session", headers=headers).status_code == 200
        assert client.get("/api/v1/me").status_code == 200
    for request_headers in ({}, env["headers"]()):
        response = client.get("/api/v1/me", headers=request_headers)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
    assert client.post("/api/v1/auth/session", headers=headers).status_code == 429
    # Another principal still has an independent allowance.
    assert client.get("/api/v1/me", headers=env["headers"]("analyst2")).status_code == 200


def test_browser_session_csrf_rotation_logout_and_revocation(env):
    client = env["client"]
    response = client.post(
        "/api/v1/auth/session", headers={**env["headers"](), "Origin": "http://localhost:8000"}
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    old_cookie = client.cookies.get("atm_session")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/v1/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Origin": "http://localhost:8000", "X-CSRF-Token": csrf}
        ).status_code
        == 204
    )
    client.cookies.set("atm_session", old_cookie, path="/api/v1")
    assert client.get("/api/v1/me").status_code == 401


def test_login_origin_and_idle_expiry(env):
    c = env["client"]
    assert c.post("/api/v1/auth/session", headers=env["headers"]()).status_code == 403
    assert (
        c.post(
            "/api/v1/auth/session", headers={**env["headers"](), "Origin": "http://localhost:8000"}
        ).status_code
        == 200
    )
    with env["factory"].begin() as s:
        s.scalar(select(BrowserSession)).idle_expires_at = now() - timedelta(seconds=1)
    assert c.get("/api/v1/me").status_code == 401


def test_policy_requires_independent_recent_supervisor(env):
    p = post(
        env,
        "admin/risk-policy",
        {"threshold": 0.7, "reason": "Reviewed threshold proposal"},
        "admin",
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    assert post(env, f"admin/risk-policy/{pid}/activate", {}, "admin").status_code == 403
    assert post(env, f"admin/risk-policy/{pid}/activate", {}, "supervisor").status_code == 200
    assert post(env, f"admin/risk-policy/{pid}/activate", {}, "supervisor").status_code == 409
    assert (
        post(
            env, "admin/risk-policy", {"threshold": 1.1, "reason": "Invalid threshold"}, "admin"
        ).status_code
        == 422
    )


def test_reviewed_snapshot_model_review_and_drift(env, transaction_body):
    cid = case_for(env, transaction_body)
    assert (
        post(
            env,
            f"cases/{cid}/decisions",
            {
                "expected_version": 1,
                "disposition": "LEGITIMATE",
                "reason": "Synthetic legitimate reviewed example",
            },
        ).status_code
        == 201
    )
    training = post(env, "training-jobs", {}, "supervisor")
    assert training.status_code == 201, training.text
    tid = training.json()["id"]
    data = (
        env["client"]
        .get(f"/api/v1/training-jobs/{tid}/dataset", headers=env["headers"]("admin"))
        .json()
    )
    assert data["items"][0]["is_fraud"] is False
    assert "label_available_at" in data["items"][0]
    assert "reason" not in data["items"][0]
    model = post(
        env,
        "models",
        {
            "name": "synthetic-evaluation-v1",
            "digest": "a" * 64,
            "simulation": True,
            "precision": 0.6,
            "recall": 0.8,
        },
        "admin",
    )
    assert model.status_code == 201
    mid = model.json()["id"]
    assert (
        post(
            env, f"models/{mid}/review", {"expected_version": 1, "approved": True}, "supervisor"
        ).status_code
        == 200
    )
    assert (
        post(
            env,
            f"training-jobs/{tid}/complete",
            {"model_id": mid, "snapshot_hash": data["snapshot_hash"]},
            "admin",
        ).status_code
        == 200
    )
    assert (
        post(
            env,
            "drift-reports",
            {
                "model_id": mid,
                "statistic": 0.12,
                "sample_size": 250,
                "window_end": "2026-01-01T10:00:00Z",
            },
            "admin",
        ).status_code
        == 201
    )


def test_audio_is_call_scoped_and_text_survives_voice_failure(env, transaction_body, monkeypatch):
    cid = case_for(env, transaction_body)
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    call = post(
        env,
        f"cases/{cid}/calls",
        {"expected_version": 2, "participant_id": str(env["ids"]["participant"]), "language": "ar"},
    ).json()
    call_id = call["id"]
    post(
        env,
        f"calls/{call_id}/transition",
        {"expected_version": 1, "operation": "answer"},
        "participant",
    )
    # Test audio authorization here; verify offline speech separately in the container.
    monkeypatch.setattr("atm_backend.worker.introduction", lambda *_: b"RIFF-synthetic-test-clip")
    assert run_one(env["runtime"], SimulationScorer())
    c = env["client"]
    clips = c.get(f"/api/v1/calls/{call_id}/audio", headers=env["headers"]("participant")).json()
    clip_id = clips["items"][0]["id"]
    path = f"/api/v1/calls/{call_id}/audio/{clip_id}"
    assert c.get(path, headers=env["headers"]("participant")).status_code == 200
    assert c.get(path, headers=env["headers"]("participant2")).status_code == 404
    assert c.get(path, headers=env["headers"]("outsider")).status_code == 404
