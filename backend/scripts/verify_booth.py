"""Exercise the running isolated booth through HTTPS and ordinary workflow routes.

Creates one scripted denial/approval/decision scenario in synthetic data. Never
accepts a remote URL or shared workspace. Requires an authenticated presentation
session supplied through ATM_PRESENTATION_SESSION and ATM_PRESENTATION_CSRF.
"""

import argparse
import json
import os
import ssl
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai", action="store_true", help="Also attempt real local Qwen analysis")
    args = parser.parse_args()
    session = os.environ.get("ATM_PRESENTATION_SESSION")
    csrf = os.environ.get("ATM_PRESENTATION_CSRF")
    if not session or not csrf:
        parser.error(
            "Sign in first; supply ATM_PRESENTATION_SESSION and ATM_PRESENTATION_CSRF "
            "through a private environment, never command-line arguments."
        )
    checks = []
    with httpx.Client(
        base_url="https://localhost:8555",
        trust_env=False,
        timeout=130,
        verify=ssl.create_default_context(cafile=str(ROOT / ".runtime/localhost.pem")),
        headers={"Origin": "https://localhost:8555"},
    ) as client:
        client.cookies.set("atm_booth_session", session, domain="localhost.local", path="/api/v1")
        client.headers["X-CSRF-Token"] = csrf

        def get(path):
            r = client.get("/api/v1/" + path)
            r.raise_for_status()
            return r.json()

        def post(path, body):
            r = client.post("/api/v1/" + path, json=body)
            r.raise_for_status()
            return r.json()

        def role(name):
            result = post("booth/role", {"role": name})
            client.headers["X-CSRF-Token"] = result["csrf_token"]
            return result["identity"]

        assert get("booth/config")["enabled"]
        assert client.get("/api/v1/me").status_code == 401
        assert (
            client.post(
                "/api/v1/booth/role",
                json={"role": "admin"},
                headers={"Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        role("ingestion")
        customers = get("booth/customers")["items"]
        assert len(customers) == 100
        checks.append(
            {
                "check": "customer_cohort",
                "customers": len(customers),
                "available_payments": sum(c["available"] for c in customers),
            }
        )
        monitor = get("booth/payments")
        assert monitor["stats"]["cleared"] > 0
        assert monitor["stats"]["flagged"] > 0
        payment = monitor["items"][0]
        original = get(f"booth/transactions/{payment['id']}/source")
        assert "is_fraud" not in original and "fraud_type" not in original
        history = get(f"booth/payments?account_id={payment['account_id']}")
        assert all(p["account_id"] == payment["account_id"] for p in history["items"])
        for priority in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            filtered = get(f"booth/payments?priority={priority}")
            assert all(p["result"]["severity"] == priority for p in filtered["items"])
        checks.append({"check": "payment_monitor_and_redacted_source", "stats": monitor["stats"]})
        analyst = role("analyst")
        assert client.get("/api/v1/admin/jobs").status_code == 403
        cases = get("cases?limit=100&state=OPEN")["items"]
        for _ in range(30):
            candidates = [c for c in cases if c["processing_status"] == "SCORED"]
            if candidates:
                break
            time.sleep(1)
            cases = get("cases?limit=100&state=OPEN")["items"]
        assert candidates, "No supervised-scored cases ready"
        cid = candidates[0]["id"]

        def case():
            return get("cases/" + cid)

        post(
            f"cases/{cid}/investigations",
            {"expected_version": case()["version"], "operation": "start"},
        )
        summary = get(f"cases/{cid}/summary")
        assert summary["transaction"]["result"]["model_version"] == "fraud_e05"
        checks.append({"check": "actual_e05_scoring", "case_id": cid})
        participant = get("team/members?role=participant")["items"][0]["id"]
        call = post(
            f"cases/{cid}/calls",
            {
                "expected_version": case()["version"],
                "participant_id": participant,
                "language": "en",
            },
        )
        connected = post(
            f"booth/calls/{call['id']}/customer-reply",
            {"expected_version": call["version"], "reply": "answer"},
        )
        assert connected["state"] == "ACTIVE"
        for _ in range(30):
            clips = get(f"calls/{call['id']}/audio")["items"]
            if clips:
                break
            time.sleep(1)
        assert clips, "Voice instructions were not generated"
        audio = client.get(f"/api/v1/calls/{call['id']}/audio/{clips[0]['id']}")
        assert audio.status_code == 200 and audio.content.startswith(b"RIFF")
        checks.append({"check": "phone_answer_and_voice", "audio_bytes": len(audio.content)})
        post(
            f"booth/calls/{call['id']}/customer-reply",
            {"expected_version": get(f"calls/{call['id']}")["version"], "reply": "denied"},
        )
        assert get(f"calls/{call['id']}")["state"] == "ENDED"
        turns = get(f"calls/{call['id']}/turns")["items"]
        assert any(t["speaker"] == "customer" for t in turns)
        checks.append({"check": "scripted_customer_denial", "call_id": call["id"]})
        if args.ai:
            result = client.post(
                f"/api/v1/cases/{cid}/agent-analysis", json={"expected_version": case()["version"]}
            )
            result.raise_for_status()
            assert result.json()["details"]["analysis"]["summary"]
            latest = get(f"cases/{cid}/findings?latest_ai=true")["items"][0]
            assert latest["id"] == result.json()["id"]
            (ROOT / ".runtime/booth/analysis-after-verification.json").write_text(
                json.dumps(latest, indent=2)
            )
            checks.append(
                {"check": "local_qwen_after_customer_denial", "http_status": result.status_code}
            )
        proposal = post(
            f"cases/{cid}/action-proposals",
            {
                "expected_version": case()["version"],
                "action": "FREEZE_ACCOUNT",
                "reason": "Scripted booth customer denied this synthetic payment",
            },
        )
        approval = {"expected_version": 1, "approved": True}
        assert (
            client.post(
                f"/api/v1/action-proposals/{proposal['id']}/approval", json=approval
            ).status_code
            == 403
        )
        supervisor = role("supervisor")
        assert supervisor["id"] != analyst["id"]
        post(f"action-proposals/{proposal['id']}/approval", approval)
        assert (
            client.post(
                f"/api/v1/action-proposals/{proposal['id']}/approval", json=approval
            ).status_code
            == 409
        )
        for _ in range(60):
            summary = get(f"cases/{cid}/summary")
            if summary["unfinished_approved_actions"] == 0:
                break
            time.sleep(1)
        assert summary["unfinished_approved_actions"] == 0
        role("analyst")
        post(
            f"cases/{cid}/decisions",
            {
                "expected_version": case()["version"],
                "disposition": "CONFIRMED_FRAUD",
                "reason": "Scripted end-to-end booth demonstration, not a real fraud conclusion",
            },
        )
        assert case()["state"] == "RESOLVED"
        checks.append({"check": "approval_execution_and_resolution", "case_id": cid})
        role("admin")
        jobs = get("admin/jobs?limit=100")["items"]
        failed = [j for j in jobs if j["state"] == "FAILED"]
        checks.append({"check": "failed_jobs_in_latest_page", "count": len(failed)})
        assert not failed
    path = ROOT / ".runtime/booth/verification.json"
    path.write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
