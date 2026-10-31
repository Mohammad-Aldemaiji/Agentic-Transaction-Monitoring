"""Presenter-controlled role play, registered ONLY in an isolated loopback booth app.

These are scripted identities, not real independent humans or real customer consent.
The shared application's authentication and authorization are unchanged.
"""

import csv
import hashlib
import io
import secrets
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, Query, Request, Response
from sqlalchemy import func, select

from atm_backend import contracts as dto
from atm_backend import services, sessions, views
from atm_backend.e05 import CHANNELS, replay_payload
from atm_backend.main import DB, Identity
from atm_backend.models import BrowserSession, Case, Principal, Transaction, now
from atm_backend.security import Actor, Problem


class RoleInput(dto.Input):
    role: Literal["ingestion", "analyst", "supervisor", "admin"]


class CustomerReply(dto.VersionInput):
    reply: Literal["answer", "confirmed", "denied", "unavailable"]


class FeedInput(dto.Input):
    count: Literal[10, 25, 100] = 25


@lru_cache(maxsize=1)
def cohort(path: str, digest: str) -> list[list[dict[str, Any]]]:
    from pathlib import Path

    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise Problem(503, "sample_traffic_unavailable")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(data.decode())):
        if row["currency"] == "SAR" and row["channel"] in CHANNELS and row["status"] == "APPROVED":
            groups[row["account_id"]].append(row)
    ordered = sorted(groups.values(), key=lambda rows: (-len(rows), rows[0]["account_id"]))
    ordinary = [rows for rows in ordered if not any(r["is_fraud"] == "1" for r in rows)][:75]
    mixed = [rows for rows in ordered if any(r["is_fraud"] == "1" for r in rows)][:25]
    return [sorted(rows, key=lambda row: row["timestamp"]) for rows in ordinary + mixed]


def register(app: FastAPI) -> None:
    if app.state.settings.environment != "booth":
        raise ValueError("Booth routes cannot be installed in a shared app")

    @app.get("/api/v1/booth/customers")
    def customers(s: DB, a: Identity) -> dict[str, Any]:
        a.require("ingestion", "analyst", "supervisor", "admin")
        cfg = app.state.settings
        counts = {
            account: count
            for account, count in s.execute(
                select(Transaction.account_id, func.count())
                .where(Transaction.team == a.team)
                .group_by(Transaction.account_id)
            ).all()
        }
        return {
            "items": [
                {
                    "name": f"Customer {index + 1:03d}",
                    "account_id": rows[0]["account_id"],
                    "available": len(rows),
                    "received": counts.get(rows[0]["account_id"], 0),
                }
                for index, rows in enumerate(cohort(str(cfg.e05_data_file), cfg.e05_data_digest))
            ]
        }

    @app.get("/api/v1/booth/payments")
    def payments(
        s: DB,
        a: Identity,
        offset: int = Query(0, ge=0, le=100000),
        account_id: str | None = None,
        priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None,
    ) -> dict[str, Any]:
        a.require("ingestion", "analyst", "supervisor", "admin")
        q = (
            select(Transaction, Case.id)
            .outerjoin(Case, Case.transaction_id == Transaction.id)
            .where(Transaction.team == a.team)
        )
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        if priority:
            q = q.where(Transaction.result["severity"].as_string() == priority)
        rows = s.execute(
            q.order_by(Transaction.received_at.desc(), Transaction.id).offset(offset).limit(51)
        ).all()
        severity = Transaction.result["severity"].as_string()
        stats = (
            s.execute(
                select(
                    func.count().label("received"),
                    func.count().filter(Transaction.status == "PENDING").label("pending"),
                    func.count().filter(Transaction.status == "FAILED").label("failed"),
                    func.count()
                    .filter((Transaction.status == "SCORED") & (Case.id.is_(None)))
                    .label("cleared"),
                    func.count().filter(Case.id.is_not(None)).label("flagged"),
                    func.count().filter(severity == "LOW").label("low"),
                    func.count().filter(severity == "MEDIUM").label("medium"),
                    func.count().filter(severity == "HIGH").label("high"),
                    func.count().filter(severity == "CRITICAL").label("critical"),
                )
                .select_from(Transaction)
                .outerjoin(Case, Case.transaction_id == Transaction.id)
                .where(Transaction.team == a.team)
            )
            .mappings()
            .one()
        )
        return {
            "items": [{**views.transaction(t), "case_id": case_id} for t, case_id in rows[:50]],
            "has_more": len(rows) > 50,
            "stats": dict(stats),
        }

    @app.get("/api/v1/booth/transactions/{transaction_id}/source")
    def source(transaction_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("ingestion", "analyst", "supervisor", "admin")
        transaction = s.get(Transaction, transaction_id)
        if transaction is None or transaction.team != a.team:
            raise Problem(404, "not_found")
        cfg = app.state.settings
        from pathlib import Path

        data = Path(cfg.e05_data_file).read_bytes()
        if hashlib.sha256(data).hexdigest() != cfg.e05_data_digest:
            raise Problem(503, "sample_traffic_unavailable")
        hidden = {
            "is_fraud",
            "fraud_type",
            "is_suspicious_aml",
            "aml_typology",
            "case_id",
            "label_source",
            "label_available_at",
            "weight_fraud",
            "weight_aml",
        }
        for row in csv.DictReader(io.StringIO(data.decode())):
            if row["txn_id"] == transaction.external_id:
                return {k: v for k, v in row.items() if k not in hidden}
        raise Problem(404, "source_payload_unavailable")

    @app.post("/api/v1/booth/feed", status_code=202)
    def feed(body: FeedInput, s: DB, a: Identity) -> dict[str, Any]:
        a.require("ingestion")
        cfg = app.state.settings
        groups = cohort(str(cfg.e05_data_file), cfg.e05_data_digest)
        # Serialize concurrent feed clicks before choosing unreceived records.
        from sqlalchemy import text

        s.execute(text("SELECT pg_advisory_xact_lock(8555)"))
        existing = set(s.scalars(select(Transaction.external_id).where(Transaction.team == a.team)))
        records = []
        for index in range(max(map(len, groups))):
            for rows in groups:
                if index < len(rows) and rows[index]["txn_id"] not in existing:
                    payload = dto.TransactionInput(**replay_payload(rows[index]))
                    records.append((payload, "sample-e05-" + payload.external_id))
                    if len(records) == body.count:
                        break
            if len(records) == body.count:
                break
        if records:
            services.ingest_many(s, a, records)
        return {"accepted": len(records), "exhausted": len(records) < body.count}

    @app.post("/api/v1/booth/role")
    def role(body: RoleInput, request: Request, response: Response, s: DB) -> dict[str, Any]:
        if request.headers.get("origin") != "https://localhost:8555":
            raise Problem(403, "origin_denied")
        presenter = None
        if not getattr(app.state, "booth_bootstrap", False):
            sessions.authenticate(request, s)
            previous = s.get(
                BrowserSession,
                sessions.digest(request.cookies.get(sessions.cookie_name(request), "")),
            )
            presenter = previous.claims.get("presenter") if previous else None
            if not presenter:
                raise Problem(401, "authentication_required")
        return select_role(body.role, request, response, s, presenter)

    @app.post("/api/v1/booth/calls/{call_id}/customer-reply")
    def customer_reply(call_id: UUID, body: CustomerReply, s: DB, a: Identity) -> dict[str, Any]:
        a.require("analyst", "supervisor")
        call, _ = services.call_access(s, a, call_id, write=True)
        services.version(call.version, body.expected_version)
        principal = s.get(Principal, call.participant_id)
        if principal is None or principal.team != "booth" or not principal.active:
            raise Problem(404, "not_found")
        customer = Actor(principal.id, "booth", "participant", now(), a.request_id)
        if body.reply == "answer":
            services.transition_call(
                s,
                customer,
                call.id,
                dto.CallTransition(expected_version=call.version, operation="answer"),
            )
            services.audit(s, a, "booth.customer_answer_simulated", call.id)
            return {"state": call.state, "version": call.version, "scripted": True}
        if call.state == "RINGING":
            services.transition_call(
                s,
                customer,
                call.id,
                dto.CallTransition(
                    expected_version=call.version,
                    operation="decline" if body.reply == "unavailable" else "answer",
                ),
            )
        if body.reply != "unavailable":
            services.add_turn(
                s,
                customer,
                call.id,
                dto.TurnInput(
                    expected_version=call.version,
                    text=(
                        "Yes, I authorized this payment."
                        if body.reply == "confirmed"
                        else "No, I did not authorize this payment."
                    ),
                ),
            )
            services.transition_call(
                s,
                customer,
                call.id,
                dto.CallTransition(expected_version=call.version, operation="end"),
            )
        elif call.state != "DECLINED":
            raise Problem(409, "invalid_transition")
        services.audit(s, a, "booth.customer_response_simulated", call.id)
        return {"state": call.state, "version": call.version, "scripted": True}


def select_role(
    selected_role: str,
    request: Request,
    response: Response,
    s: Any,
    presenter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    principal = s.scalar(
        select(Principal).where(
            Principal.team == "booth",
            Principal.subject == "booth-" + selected_role,
            Principal.active.is_(True),
        )
    )
    if principal is None:
        raise Problem(503, "booth_not_initialized")
    previous = s.get(
        BrowserSession, sessions.digest(request.cookies.get(sessions.cookie_name(request), ""))
    )
    if previous:
        previous.revoked = True
    token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    timestamp = int(now().timestamp())
    claims = {
        "iss": "https://booth.local",
        "sub": principal.subject,
        "iat": timestamp,
        "exp": timestamp + 3600,
        "auth_time": timestamp,
    }
    if presenter is not None:
        claims["presenter"] = presenter
        claims["exp"] = presenter["workspace_expires_at"]
    s.add(
        BrowserSession(
            token_hash=sessions.digest(token),
            csrf_hash=sessions.digest(csrf),
            principal_id=principal.id,
            claims=claims,
            expires_at=datetime.fromtimestamp(claims["exp"], now().tzinfo),
            idle_expires_at=datetime.fromtimestamp(claims["exp"], now().tzinfo),
        )
    )
    response.set_cookie(
        sessions.cookie_name(request),
        token,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/api/v1",
        max_age=max(1, claims["exp"] - timestamp),
    )
    actor = Actor(principal.id, principal.team, principal.role, now(), request.state.request_id)
    services.audit(s, actor, "booth.role_selected", principal.id)
    return {
        "csrf_token": csrf,
        "identity": {
            "id": principal.id,
            "team": "booth",
            "role": principal.role,
            "booth_mode": True,
            "sample_traffic_enabled": principal.role in ("analyst", "supervisor", "admin"),
        },
    }
