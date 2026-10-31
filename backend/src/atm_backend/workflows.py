"""Application workflows and read APIs used by the analyst and customer clients."""

import base64
import binascii
import hashlib
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, Query, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from atm_backend import agent_workflow, queries, views
from atm_backend import contracts as dto
from atm_backend import services as svc
from atm_backend.local_agent import AgentUnavailable, LocalAgent
from atm_backend.local_voice import run_voice
from atm_backend.main import DB, Identity, PageSize
from atm_backend.models import (
    Audit,
    Call,
    Case,
    Decision,
    Event,
    Execution,
    Investigation,
    Job,
    ModelVersion,
    Note,
    Principal,
    Proposal,
    TrainingRequest,
    Transaction,
    now,
)
from atm_backend.security import Problem
from atm_backend.voice import VoiceUnavailable


def register(app: FastAPI) -> None:
    app.state.local_agent = LocalAgent(app.state.settings)

    @app.post("/api/v1/calls/{call_id}/transcribe")
    def transcribe_call(
        call_id: UUID, body: dto.TranscriptionInput, s: DB, a: Identity
    ) -> dict[str, Any]:
        a.require("participant")
        call, _ = svc.call_access(s, a, call_id, write=True)
        svc.version(call.version, body.expected_version)
        if call.state != "ACTIVE" or call.expires_at <= now():
            raise Problem(409, "call_not_active")
        directory = app.state.settings.whisper_model_dir
        if not directory:
            raise Problem(503, "local_transcription_disabled")
        try:
            content = base64.b64decode(body.audio_base64, validate=True)
        except (ValueError, binascii.Error):
            raise Problem(422, "invalid_audio") from None
        if not content or len(content) > 65536:
            raise Problem(422, "invalid_audio")
        language = call.language
        svc.audit(s, a, "call.transcription_requested", call_id)
        s.commit()
        try:
            draft = run_voice("transcribe", directory, language, content).decode("utf-8")
        except (VoiceUnavailable, UnicodeError):
            raise Problem(503, "local_transcription_unavailable") from None
        return {"text": draft, "draft_only": True}

    @app.post("/api/v1/cases/{case_id}/agent-analysis")
    def analyse_case(case_id: UUID, body: dto.VersionInput, s: DB, a: Identity) -> dict[str, Any]:
        if not app.state.settings.agent_enabled:
            raise Problem(503, "local_agent_disabled")
        evidence = agent_workflow.collect(s, a, case_id, body.expected_version)
        # Release locks and the connection before potentially slow local inference.
        s.commit()
        try:
            result = app.state.local_agent.analyse(evidence)
        except AgentUnavailable:
            raise Problem(503, "local_agent_unavailable") from None
        with app.state.factory.begin() as fresh:
            row = agent_workflow.save(
                fresh, a, case_id, evidence, result, app.state.settings.agent_model
            )
            return views.fields(row, "id", "details", "created_at")

    @app.get("/api/v1/dashboard")
    def dashboard(s: DB, a: Identity) -> dict[str, Any]:
        return queries.dashboard(s, a)

    @app.get("/api/v1/transactions")
    def transactions(
        s: DB,
        a: Identity,
        limit: PageSize = 50,
        after: UUID | None = None,
        status: Literal["PENDING", "SCORED", "FAILED"] | None = None,
        account_id: dto.Identifier | None = None,
    ) -> dict[str, Any]:
        a.require("ingestion", *svc.READ_ROLES)
        q = select(Transaction).where(Transaction.team == a.team)
        if a.role != "ingestion":
            q = q.where(
                Transaction.id.in_(queries.visible_cases(a).with_only_columns(Case.transaction_id))
            )
        if status:
            q = q.where(Transaction.status == status)
        if account_id:
            q = q.where(Transaction.account_id == account_id)
        if after:
            q = q.where(Transaction.id > after)
        rows = list(s.scalars(q.order_by(Transaction.id).limit(limit + 1)))
        return {
            "items": [views.transaction(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.get("/api/v1/transactions/{transaction_id}/processing")
    def processing(transaction_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("ingestion", *svc.READ_ROLES)
        t = s.scalar(
            select(Transaction).where(Transaction.id == transaction_id, Transaction.team == a.team)
        )
        if t is None:
            raise Problem(404, "not_found")
        case_id = s.scalar(select(Case.id).where(Case.transaction_id == t.id))
        if a.role != "ingestion":
            if case_id is None:
                raise Problem(404, "not_found")
            svc.case_access(s, a, case_id)
        job = s.scalar(select(Job).where(Job.object_id == t.id, Job.kind == "score"))
        return {
            "transaction_id": t.id,
            "status": t.status,
            "case_id": case_id,
            "scoring_configured": app.state.settings.scoring_mode != "disabled",
            "features_available": t.features is not None,
            "job": views.fields(job, "state", "attempts", "error_code", "available_at")
            if job
            else None,
        }

    @app.get("/api/v1/team/members")
    def members(
        s: DB,
        a: Identity,
        role: Literal["analyst", "participant"],
        limit: PageSize = 50,
        after: UUID | None = None,
    ) -> dict[str, Any]:
        a.require(*svc.REVIEW_ROLES)
        if role == "analyst":
            a.require("supervisor")
        q = select(Principal).where(
            Principal.team == a.team, Principal.role == role, Principal.active.is_(True)
        )
        if after:
            q = q.where(Principal.id > after)
        rows = list(s.scalars(q.order_by(Principal.id).limit(limit + 1)))
        return {
            "items": [views.fields(r, "id", "role") for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.get("/api/v1/cases/{case_id}/calls")
    def case_calls(
        case_id: UUID, s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        q = select(Call).join(Investigation).where(Investigation.case_id == case_id)
        if after:
            q = q.where(Call.id > after)
        rows = list(s.scalars(q.order_by(Call.id).limit(limit + 1)))
        return {
            "items": [views.call(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.post("/api/v1/calls/{call_id}/verification")
    def verification(
        call_id: UUID, body: dto.VerificationInput, s: DB, a: Identity
    ) -> dict[str, Any]:
        return views.call(svc.verify_customer(s, a, call_id, body))

    @app.post("/api/v1/cases/{case_id}/findings", status_code=201)
    def finding(case_id: UUID, body: dto.FindingInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.fields(svc.record_finding(s, a, case_id, body), "id", "details", "created_at")

    @app.get("/api/v1/cases/{case_id}/findings")
    def findings(
        case_id: UUID,
        s: DB,
        a: Identity,
        limit: PageSize = 50,
        latest_ai: bool = False,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        if latest_ai:
            row = s.scalar(
                select(Event)
                .where(
                    Event.case_id == case_id,
                    Event.kind == "investigation.finding",
                    Event.details["source"].as_string() == "local_llm",
                )
                .order_by(Event.id.desc())
                .limit(1)
            )
            return {
                "items": [views.fields(row, "id", "details", "created_at")] if row else [],
                "next_cursor": None,
            }
        rows = list(
            s.scalars(
                select(Event)
                .where(
                    Event.case_id == case_id,
                    Event.kind == "investigation.finding",
                    Event.id > after,
                )
                .order_by(Event.id)
                .limit(limit + 1)
            )
        )
        return {
            "items": [views.fields(r, "id", "details", "created_at") for r in rows[:limit]],
            "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
        }

    @app.get("/api/v1/cases/{case_id}/summary")
    def summary(case_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        # Scope and lock in the same statement; recheck assignment after a concurrent writer.
        c = s.scalar(queries.visible_cases(a).where(Case.id == case_id).with_for_update())
        if c is None:
            raise Problem(404, "not_found")
        t = s.get(Transaction, c.transaction_id)
        if t is None:
            raise Problem(409, "case_transaction_missing")
        investigation = s.scalar(select(Investigation).where(Investigation.case_id == c.id))
        decision = s.scalar(select(Decision).where(Decision.case_id == c.id))
        live = s.scalar(
            select(Call)
            .join(Investigation)
            .where(Investigation.case_id == c.id, Call.state.in_(svc.LIVE_CALL))
        )
        pending = s.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(
                Proposal.case_id == c.id, Proposal.state == "PENDING", Proposal.expires_at > now()
            )
        )
        unfinished = s.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(
                Proposal.case_id == c.id,
                Proposal.state == "APPROVED",
                ~select(Execution.id).where(Execution.proposal_id == Proposal.id).exists(),
            )
        )
        svc.audit(s, a, "case.summary_read", c.id)
        return {
            "case": queries.queue_item(c, t),
            "transaction": views.transaction(t),
            "investigation": views.fields(investigation, "id", "state", "version")
            if investigation
            else None,
            "decision": views.fields(
                decision, "id", "disposition", "reason", "reviewer_id", "created_at"
            )
            if decision
            else None,
            "active_call": views.call(live) if live else None,
            "pending_proposals": pending,
            "unfinished_approved_actions": unfinished,
            "evidence_version": c.version,
            "local_agent_enabled": app.state.settings.agent_enabled,
            "counts": {
                "notes": s.scalar(
                    select(func.count()).select_from(Note).where(Note.case_id == c.id)
                ),
                "findings": s.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.case_id == c.id, Event.kind == "investigation.finding")
                ),
            },
        }

    @app.get("/api/v1/training-jobs")
    def training_jobs(
        s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        q = select(TrainingRequest).where(TrainingRequest.team == a.team)
        if after:
            q = q.where(TrainingRequest.id > after)
        rows = list(s.scalars(q.order_by(TrainingRequest.id).limit(limit + 1)))
        return {
            "items": [
                views.fields(r, "id", "state", "snapshot_hash", "model_id", "created_at")
                for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.post("/api/v1/training-jobs/{job_id}/cancel")
    def cancel_training(
        job_id: UUID, body: dto.TrainingCancel, s: DB, a: Identity
    ) -> dict[str, Any]:
        a.require("supervisor")
        if (now() - a.auth_time).total_seconds() > app.state.settings.approval_auth_age_seconds:
            raise Problem(403, "fresh_authentication_required")
        row = s.scalar(
            select(TrainingRequest)
            .where(TrainingRequest.id == job_id, TrainingRequest.team == a.team)
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found")
        if row.state != "READY":
            raise Problem(409, "invalid_transition")
        row.state = "CANCELLED"
        row.cancellation_reason = body.reason
        svc.audit(s, a, "training.cancelled", row.id)
        return views.fields(row, "id", "state")

    @app.get("/api/v1/models/{model_id}")
    def model_detail(model_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        row = s.scalar(
            select(ModelVersion).where(ModelVersion.id == model_id, ModelVersion.team == a.team)
        )
        if row is None:
            raise Problem(404, "not_found")
        return {
            **views.fields(
                row, "id", "name", "digest", "simulation", "metrics", "state", "created_at"
            ),
            "deployment_status": "CONFIGURED"
            if (
                app.state.settings.scoring_mode == "remote"
                and app.state.settings.scoring_model_name == row.name
                and app.state.settings.scoring_model_digest == row.digest
                and row.state == "APPROVED"
                and not row.simulation
            )
            else "NOT_DEPLOYED",
        }

    @app.get("/api/v1/audit/export")
    def export_audit(
        s: DB,
        a: Identity,
        after: Annotated[int, Query(ge=0)] = 0,
        through: Annotated[int | None, Query(ge=0)] = None,
    ) -> Response:
        a.require("auditor")
        if (now() - a.auth_time).total_seconds() > app.state.settings.approval_auth_age_seconds:
            raise Problem(403, "fresh_authentication_required")
        q = select(Audit).where(Audit.team == a.team, Audit.id > after)
        if through is not None:
            q = q.where(Audit.id <= through)
        # One statement observes a consistent committed snapshot. Never silently truncate.
        rows = list(s.scalars(q.order_by(Audit.id).limit(10001)))
        if len(rows) > 10000:
            raise Problem(409, "export_limit_exceeded")
        data = jsonable_encoder(
            [
                views.fields(r, "id", "actor", "kind", "object_id", "request_id", "created_at")
                for r in rows
            ]
        )
        encoded = json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        payload = {
            "format": "atm-audit-export-v1",
            "count": len(rows),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "hash_encoding": "UTF-8 JSON items, sorted keys, compact separators, unescaped Unicode",
            "items": data,
        }
        svc.audit(s, a, "audit.exported", str(rows[-1].id if rows else after))
        return Response(
            json.dumps(payload, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="audit-export.json"'},
        )
