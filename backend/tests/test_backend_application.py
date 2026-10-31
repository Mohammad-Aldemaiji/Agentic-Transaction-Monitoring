"""Application-level acceptance tests against isolated Supabase PostgreSQL."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from test_workflows import case_for, post, submit

from atm_backend.adapters import DisabledScorer, Score
from atm_backend.features import build_features
from atm_backend.models import Audit, Event, Job, Transaction, now
from atm_backend.worker import run_one


def get(env, path, who="analyst", **params):
    response = env["client"].get("/api/v1/" + path, headers=env["headers"](who), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_features_are_point_in_time_currency_safe_and_immutable(env, transaction_body):
    anchor = now() - timedelta(days=1)
    received = now() - timedelta(hours=1)
    rows = []
    # At 1h and 7d boundaries included; future-event, late-received, other-account/team excluded.
    for index, hours, currency, amount, account, team, late in [
        (0, 1, "SAR", 100, "target", "alpha", False),
        (1, 2, "SAR", 300, "target", "alpha", False),
        (2, 24, "USD", 90000, "target", "alpha", False),
        (3, 168, "SAR", 500, "target", "alpha", False),
        (4, 169, "SAR", 90000, "target", "alpha", False),
        (5, -1, "SAR", 90000, "target", "alpha", False),
        (6, 1, "SAR", 90000, "target", "alpha", True),
        (7, 1, "SAR", 90000, "another", "alpha", False),
        (8, 1, "SAR", 90000, "target", "beta", False),
    ]:
        rows.append(
            Transaction(
                id=uuid4(),
                team=team,
                external_id=str(index),
                idempotency_key=str(index),
                payload_hash="a" * 64,
                account_id=account,
                amount_minor=amount,
                currency=currency,
                channel="TRANSFER",
                occurred_at=anchor - timedelta(hours=hours),
                received_at=received + (timedelta(minutes=1) if late else -timedelta(minutes=1)),
                context={"device_id": "known", "location_country": "SA"},
            )
        )
    target = Transaction(
        id=uuid4(),
        team="alpha",
        external_id="target",
        idempotency_key="target",
        payload_hash="b" * 64,
        account_id="target",
        amount_minor=700,
        currency="SAR",
        channel="TRANSFER",
        occurred_at=anchor,
        received_at=received,
        context={"device_id": "known", "destination_account_id": "new"},
    )
    with env["factory"].begin() as s:
        s.add_all([*rows, target])
    with env["runtime"].begin() as s:
        t = s.get(Transaction, target.id)
        snapshot = build_features(s, t)
        t.features = snapshot
    v = snapshot["values"]
    assert [v["count_1h"], v["count_24h"], v["count_7d"]] == [1, 3, 4]
    assert [v["amount_minor_1h"], v["amount_minor_24h"], v["amount_minor_7d"]] == [100, 400, 900]
    assert v["mean_amount_minor_7d"] == 300
    assert v["new_device_id_7d"] is False
    assert v["new_destination_account_id_7d"] is True
    assert v["new_location_country_7d"] is None
    assert snapshot["late_event"] and not snapshot["cold_start"]
    for column, value in [
        ("features", "'{}'::jsonb"),
        ("amount_minor", "1"),
        ("context", "'{}'::jsonb"),
    ]:
        with pytest.raises(IntegrityError), env["runtime"].begin() as s:
            # Column/value are test constants, never user data.
            s.execute(
                text(f"UPDATE atm_test.transactions SET {column}={value} WHERE id=:id"),
                {"id": target.id},
            )


def test_unavailable_scoring_is_a_complete_manual_review_workflow(env, transaction_body):
    response = submit(
        env,
        {
            **transaction_body,
            "context": {"device_id": "synthetic-device", "merchant_category": "5411"},
        },
    )
    assert response.status_code == 202
    tid = UUID(response.json()["id"])
    for _attempt in range(5):
        with env["factory"].begin() as s:
            job = s.scalar(select(Job).where(Job.kind == "score"))
            job.available_at = now() - timedelta(seconds=1)
        assert run_one(env["runtime"], DisabledScorer())
    status = get(env, f"transactions/{tid}/processing", "ingestion")
    assert status["status"] == "FAILED" and status["features_available"]
    assert "scoring_configured" in status and "scoring_available" not in status
    cid = status["case_id"]
    assert cid
    queue = get(env, "cases", severity="UNSCORED")
    assert queue["items"][0]["risk_score"] is None
    assert queue["items"][0]["review_reason"] == "SCORING_UNAVAILABLE"
    summary = get(env, f"cases/{cid}/summary")
    assert summary["transaction"]["result"] is None
    assert summary["transaction"]["features"]["cold_start"]
    with env["factory"]() as s:
        job_id = s.scalar(select(Job.id).where(Job.kind == "score"))
    assert post(env, f"admin/jobs/{job_id}/retry", {}, "admin").status_code == 409
    assert (
        post(
            env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"}
        ).status_code
        == 200
    )
    finding = post(
        env,
        f"cases/{cid}/findings",
        {
            "expected_version": 2,
            "category": "TRANSACTION_PATTERN",
            "summary": "Manual review needed because scoring is unavailable",
            "recommendation": "CONTACT_CUSTOMER",
            "evidence_transaction_ids": [str(tid)],
        },
    )
    assert finding.status_code == 201, finding.text
    call = post(
        env,
        f"cases/{cid}/calls",
        {"expected_version": 3, "participant_id": str(env["ids"]["participant"]), "language": "ar"},
    ).json()
    path = f"calls/{call['id']}"
    assert (
        post(
            env, path + "/transition", {"expected_version": 1, "operation": "answer"}, "participant"
        ).status_code
        == 200
    )
    verify = post(
        env,
        path + "/verification",
        {"expected_version": 2, "response": "NOT_AUTHORIZED"},
        "participant",
    )
    assert verify.status_code == 200 and verify.json()["state"] == "ENDED"
    assert (
        post(
            env,
            path + "/verification",
            {"expected_version": 2, "response": "AUTHORIZED"},
            "participant",
        ).status_code
        == 409
    )
    summary = get(env, f"cases/{cid}/summary")
    assert summary["counts"]["findings"] == 1 and summary["decision"] is None
    assert summary["pending_proposals"] == 0  # Customer speech never approves a bank action.
    proposal = post(
        env,
        f"cases/{cid}/action-proposals",
        {
            "expected_version": summary["evidence_version"],
            "action": "HOLD_TRANSFER",
            "reason": "Customer denies transaction; independent review required",
        },
    ).json()
    assert (
        post(
            env,
            f"action-proposals/{proposal['id']}/approval",
            {"expected_version": 1, "approved": True},
            "supervisor",
        ).status_code
        == 200
    )
    # Avoid needing offline speech in the application-logic test; execute only the selected action.
    with env["factory"].begin() as s:
        for job in s.scalars(select(Job).where(Job.kind == "voice")):
            job.state = "DONE"
    assert run_one(env["runtime"], DisabledScorer())
    assert get(env, f"action-proposals/{proposal['id']}/execution")["status"] == "SUCCEEDED"
    decision = post(
        env,
        f"cases/{cid}/decisions",
        {
            "expected_version": summary["evidence_version"],
            "disposition": "CONFIRMED_FRAUD",
            "reason": "Evidence and recorded customer denial reviewed",
        },
    )
    assert decision.status_code == 201, decision.text
    assert get(env, "dashboard")["decisions"]["CONFIRMED_FRAUD"] == 1


def test_ranked_queue_filters_and_cross_assignment_scope(env, transaction_body):
    class Provider:
        def score(self, transaction, features):
            assert features["version"] == "account-windows-v1"
            return Score(True, "queue-test", transaction.amount_minor / 100, True, ["TEST"])

    ids = []
    for index, amount in enumerate([90, 90, 50, 20]):
        response = submit(
            env,
            {
                **transaction_body,
                "external_id": str(index),
                "account_id": "account-" + str(index),
                "amount_minor": amount,
            },
            str(index),
        )
        assert response.status_code == 202
        assert run_one(env["runtime"], Provider())
    first = get(env, "cases", limit=1, order="risk")
    ids.append(first["items"][0]["id"])
    cursor = first["next_cursor"]
    risks = [first["items"][0]["risk_score"]]
    while cursor:
        page = get(env, "cases", limit=1, order="risk", after=cursor)
        ids.extend(i["id"] for i in page["items"])
        risks.extend(i["risk_score"] for i in page["items"])
        cursor = page["next_cursor"]
    assert len(set(ids)) == 4 and risks == [90, 90, 50, 20]
    assert len(get(env, "cases", severity="CRITICAL")["items"]) == 2
    assigned = env["client"].put(
        f"/api/v1/cases/{ids[0]}/assignment",
        headers=env["headers"]("supervisor"),
        json={"expected_version": 1, "analyst_id": str(env["ids"]["analyst2"])},
    )
    assert assigned.status_code == 200
    assert len(get(env, "cases")["items"]) == 3
    assert sum(get(env, "dashboard")["cases"].values()) == 3
    assert len(get(env, "transactions")["items"]) == 3
    assert get(env, "cases", "outsider")["items"] == []
    assert (
        env["client"].get(f"/api/v1/cases/{ids[0]}/summary", headers=env["headers"]()).status_code
        == 404
    )
    assert (
        env["client"]
        .get("/api/v1/cases", params={"after": ids[0], "order": "risk"}, headers=env["headers"]())
        .status_code
        == 422
    )
    assert get(env, "team/members", "supervisor", role="analyst")["items"]
    assert (
        env["client"].get("/api/v1/team/members?role=analyst", headers=env["headers"]()).status_code
        == 403
    )


@pytest.mark.parametrize("language,response", [("en", "AUTHORIZED"), ("ar", "UNSURE")])
def test_structured_customer_response_is_scoped_and_keeps_human_decision(
    env, transaction_body, language, response
):
    cid = case_for(env, transaction_body)
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    call = post(
        env,
        f"cases/{cid}/calls",
        {
            "expected_version": 2,
            "participant_id": str(env["ids"]["participant"]),
            "language": language,
        },
    ).json()
    path = f"calls/{call['id']}"
    post(env, path + "/transition", {"expected_version": 1, "operation": "answer"}, "participant")
    body = {"expected_version": 2, "response": response}
    assert post(env, path + "/verification", body, "participant2").status_code == 404
    assert post(env, path + "/verification", body, "analyst").status_code == 403
    assert post(env, path + "/verification", body, "participant").status_code == 200
    turns = get(env, path + "/turns", "participant")["items"]
    assert len(turns) == 3 and turns[1]["text"] == response and turns[-1]["speaker"] == "bank"
    summary = get(env, f"cases/{cid}/summary")
    assert summary["decision"] is None and summary["case"]["state"] == "IN_REVIEW"
    assert len(get(env, f"cases/{cid}/calls")["items"]) == 1


def test_finding_references_and_audit_export_are_protected(env, transaction_body):
    cid = case_for(env, transaction_body)
    post(env, f"cases/{cid}/investigations", {"expected_version": 1, "operation": "start"})
    body = {
        "expected_version": 2,
        "category": "ACCOUNT_HISTORY",
        "summary": "Review account activity",
        "recommendation": "REVIEW",
        "evidence_transaction_ids": [str(uuid4())],
    }
    assert post(env, f"cases/{cid}/findings", body).status_code == 422
    body["evidence_transaction_ids"] = []
    assert post(env, f"cases/{cid}/findings", body, "auditor").status_code == 403
    assert post(env, f"cases/{cid}/findings", body).status_code == 201
    assert post(env, f"cases/{cid}/findings", body).status_code == 409
    assert len(get(env, f"cases/{cid}/findings")["items"]) == 1
    assert env["client"].get("/api/v1/audit/export", headers=env["headers"]()).status_code == 403
    export = get(env, "audit/export", "auditor")
    encoded = json.dumps(
        export["items"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == export["sha256"]
    assert export["count"] == len(export["items"])
    with env["factory"]() as s:
        assert (
            s.scalar(select(func.count()).select_from(Audit).where(Audit.kind == "audit.exported"))
            == 1
        )
        event_id = s.scalar(select(Event.id).where(Event.kind == "investigation.finding"))
    with pytest.raises(ProgrammingError) as denied, env["runtime"].begin() as s:
        s.execute(
            text("UPDATE atm_test.case_events SET details='{}' WHERE id=:id"), {"id": event_id}
        )
    assert denied.value.orig.sqlstate == "42501"
    with pytest.raises(DBAPIError) as protected, env["factory"].begin() as s:
        s.execute(
            text("UPDATE atm_test.case_events SET details='{}' WHERE id=:id"), {"id": event_id}
        )
    assert protected.value.orig.sqlstate == "P0001"


def test_training_cancellation_preserves_snapshot_and_prevents_completion(env, transaction_body):
    cid = case_for(env, transaction_body)
    post(
        env,
        f"cases/{cid}/decisions",
        {
            "expected_version": 1,
            "disposition": "LEGITIMATE",
            "reason": "Reviewed synthetic transfer",
        },
    )
    training = post(env, "training-jobs", {}, "supervisor").json()
    tid = training["id"]
    snapshot = get(env, f"training-jobs/{tid}/dataset", "admin")
    assert snapshot["items"][0]["features"]["version"] == "account-windows-v1"
    assert len(get(env, "training-jobs", "admin")["items"]) == 1
    assert (
        post(
            env,
            f"training-jobs/{tid}/cancel",
            {"reason": "Dataset review needs correction"},
            "admin",
        ).status_code
        == 403
    )
    assert (
        post(
            env,
            f"training-jobs/{tid}/cancel",
            {"reason": "Dataset review needs correction"},
            "supervisor",
        ).status_code
        == 200
    )
    status = get(env, f"training-jobs/{tid}", "admin")
    assert status["state"] == "CANCELLED" and status["snapshot_hash"] == snapshot["snapshot_hash"]
    assert status["cancellation_reason"] == "Dataset review needs correction"
    assert (
        post(
            env, f"training-jobs/{tid}/cancel", {"reason": "Repeat cancellation"}, "supervisor"
        ).status_code
        == 409
    )


def test_remote_model_requires_independent_registered_approval(env, transaction_body):
    import httpx
    from test_scoring_contract import result

    from atm_backend.scoring import RemoteScorer

    response = submit(env, transaction_body)
    tid = UUID(response.json()["id"])
    cfg = env["settings"].model_copy(
        update={
            "scoring_mode": "remote",
            "scoring_url": "https://scorer.example/score",
            "scoring_model_name": "approved-v1",
            "scoring_model_digest": "a" * 64,
        }
    )
    from pydantic import SecretStr

    cfg.scoring_token = SecretStr("test-only")
    requests = []

    def handle(request):
        requests.append(request)
        with env["factory"]() as s:
            t = s.get(Transaction, tid)
        return httpx.Response(200, json=result(t))

    scorer = RemoteScorer(cfg, transport=httpx.MockTransport(handle))
    try:
        assert run_one(env["runtime"], scorer)
        assert requests == []
        registered = post(
            env,
            "models",
            {
                "name": "approved-v1",
                "digest": "a" * 64,
                "simulation": False,
                "precision": 0.9,
                "recall": 0.9,
            },
            "admin",
        )
        assert registered.status_code == 201
        mid = registered.json()["id"]
        assert (
            post(
                env, f"models/{mid}/review", {"expected_version": 1, "approved": True}, "supervisor"
            ).status_code
            == 200
        )
        with env["factory"].begin() as s:
            job = s.scalar(select(Job).where(Job.object_id == tid))
            job.available_at = now() - timedelta(seconds=1)
        assert run_one(env["runtime"], scorer)
        assert len(requests) == 1
        status = get(env, f"transactions/{tid}", "ingestion")
        assert status["status"] == "SCORED"
        assert len(status["result"]["contributions"]) == 5
        assert get(env, f"models/{mid}", "admin")["deployment_status"] == "NOT_DEPLOYED"
    finally:
        scorer.close()


def test_card_action_targets_only_the_transaction_card(env, transaction_body):
    cid = case_for(
        env, {**transaction_body, "channel": "CARD", "context": {"card_id": "synthetic-card-1"}}
    )
    response = post(
        env,
        f"cases/{cid}/action-proposals",
        {"expected_version": 1, "action": "BLOCK_CARD", "reason": "Review matching card only"},
    )
    assert response.status_code == 201
    assert response.json()["target"] == "synthetic-card-1"
    no_card = case_for(env, {**transaction_body, "external_id": str(uuid4()), "channel": "CARD"})
    assert (
        post(
            env,
            f"cases/{no_card}/action-proposals",
            {
                "expected_version": 1,
                "action": "BLOCK_CARD",
                "reason": "Missing card must not freeze account",
            },
        ).status_code
        == 422
    )


def test_optional_context_preserves_old_ingestion_retry_keys(env, transaction_body):
    from atm_backend.contracts import TransactionInput

    payload = TransactionInput(**transaction_body)
    old_digest = hashlib.sha256(payload.model_dump_json(exclude={"context"}).encode()).hexdigest()
    legacy_id = uuid4()
    with env["factory"].begin() as s:
        s.add(
            Transaction(
                id=legacy_id,
                team="alpha",
                idempotency_key="legacy-key",
                payload_hash=old_digest,
                **payload.model_dump(exclude={"context"}),
            )
        )
    retry = submit(env, transaction_body, "legacy-key")
    assert retry.status_code == 202 and retry.json()["id"] == str(legacy_id)
    explicit_unknown = submit(
        env, {**transaction_body, "context": {"device_id": None}}, "legacy-key"
    )
    assert explicit_unknown.status_code == 202 and explicit_unknown.json()["id"] == str(legacy_id)
    changed = submit(
        env, {**transaction_body, "context": {"device_id": "new-device"}}, "legacy-key"
    )
    assert changed.status_code == 409
