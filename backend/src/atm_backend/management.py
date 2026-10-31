"""Governed configuration, ML handoffs and operational recovery APIs."""

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Response
from sqlalchemy import func, select, text

from atm_backend import contracts as dto
from atm_backend import services as svc
from atm_backend import views
from atm_backend.main import DB, Identity, PageSize
from atm_backend.models import (
    AudioClip,
    Case,
    Decision,
    DriftReport,
    Job,
    ModelVersion,
    PolicyVersion,
    TrainingRequest,
    Transaction,
    now,
)
from atm_backend.security import Problem


def policy_view(row: PolicyVersion) -> dict[str, Any]:
    return views.fields(
        row,
        "id",
        "version",
        "threshold",
        "state",
        "reason",
        "daily_review_capacity",
        "medium_threshold",
        "high_threshold",
        "critical_threshold",
    )


def register(app: FastAPI) -> None:
    @app.get("/api/v1/calls/{call_id}/audio")
    def clips(call_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        svc.call_access(s, a, call_id)
        clip = s.scalar(select(AudioClip).where(AudioClip.call_id == call_id))
        job_state = (
            s.scalar(
                select(Job.state).where(Job.object_id == call_id, Job.kind == "voice").limit(1)
            )
            if clip is None
            else None
        )
        return {
            "status": "READY"
            if clip
            else "FAILED"
            if job_state in ("FAILED", "DONE")
            else "PENDING"
            if job_state
            else "NOT_REQUESTED",
            "items": [{"id": clip.id, "content_type": "audio/wav"}] if clip else [],
            "text_available": True,
        }

    @app.get("/api/v1/calls/{call_id}/audio/{clip_id}")
    def audio(call_id: UUID, clip_id: UUID, s: DB, a: Identity) -> Response:
        svc.call_access(s, a, call_id)
        clip = s.scalar(
            select(AudioClip).where(AudioClip.id == clip_id, AudioClip.call_id == call_id)
        )
        if not clip:
            raise Problem(404, "not_found")
        svc.audit(s, a, "call.audio_read", call_id)
        return Response(
            content=clip.content,
            media_type="audio/wav",
            headers={"Content-Disposition": 'inline; filename="simulation.wav"'},
        )

    @app.post("/api/v1/admin/jobs/{job_id}/retry")
    def retry(job_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        job = s.scalar(select(Job).where(Job.id == job_id, Job.team == a.team).with_for_update())
        if not job:
            raise Problem(404, "not_found")
        if job.state != "FAILED":
            raise Problem(409, "job_not_failed")
        if job.kind == "score" and s.scalar(
            select(Case.id).where(Case.transaction_id == job.object_id)
        ):
            raise Problem(409, "manual_review_case_exists")
        job.state, job.attempts, job.error_code, job.available_at = "PENDING", 0, None, now()
        if job.kind == "score":
            row = s.get(Transaction, job.object_id)
            if row is None:
                raise Problem(409, "job_transaction_missing")
            row.status = "PENDING"
        svc.audit(s, a, "job.retried", job.id)
        return {"id": job.id, "state": job.state}

    @app.post("/api/v1/admin/risk-policy", status_code=201)
    def policy(body: dto.PolicyInput, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "policy:" + a.team},
        )
        version = 1 + (
            s.scalar(select(func.max(PolicyVersion.version)).where(PolicyVersion.team == a.team))
            or 0
        )
        row = PolicyVersion(
            team=a.team,
            version=version,
            threshold=body.threshold,
            daily_review_capacity=body.daily_review_capacity,
            medium_threshold=body.medium_threshold,
            high_threshold=body.high_threshold,
            critical_threshold=body.critical_threshold,
            reason=body.reason,
            created_by=a.id,
        )
        s.add(row)
        s.flush()
        svc.audit(s, a, "policy.proposed", row.id)
        return policy_view(row)

    @app.post("/api/v1/admin/risk-policy/{policy_id}/activate")
    def activate(policy_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("supervisor")
        if (now() - a.auth_time).total_seconds() > app.state.settings.approval_auth_age_seconds:
            raise Problem(403, "fresh_authentication_required")
        s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "policy:" + a.team},
        )
        row = s.scalar(
            select(PolicyVersion).where(PolicyVersion.id == policy_id, PolicyVersion.team == a.team)
        )
        if not row:
            raise Problem(404, "not_found")
        if row.created_by == a.id:
            raise Problem(403, "independent_approval_required")
        if row.state != "DRAFT":
            raise Problem(409, "invalid_transition")
        for old in s.scalars(
            select(PolicyVersion).where(
                PolicyVersion.team == a.team, PolicyVersion.state == "ACTIVE"
            )
        ):
            old.state = "RETIRED"
        s.flush()
        row.state, row.approved_by = "ACTIVE", a.id
        svc.audit(s, a, "policy.activated", row.id)
        return policy_view(row)

    @app.get("/api/v1/admin/risk-policy")
    def policies(
        s: DB, a: Identity, limit: PageSize = 50, before: int | None = None
    ) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        q = select(PolicyVersion).where(PolicyVersion.team == a.team)
        if before:
            q = q.where(PolicyVersion.version < before)
        rows = list(s.scalars(q.order_by(PolicyVersion.version.desc()).limit(limit + 1)))
        return {
            "items": [policy_view(r) for r in rows[:limit]],
            "next_cursor": rows[limit - 1].version if len(rows) > limit else None,
        }

    @app.post("/api/v1/models", status_code=201)
    def model(body: dto.ModelInput, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        row = ModelVersion(
            team=a.team,
            name=body.name,
            digest=body.digest,
            simulation=body.simulation,
            metrics={"precision": body.precision, "recall": body.recall},
            registered_by=a.id,
        )
        s.add(row)
        s.flush()
        svc.audit(s, a, "model.registered", row.id)
        return views.fields(row, "id", "name", "digest", "state", "simulation", "metrics")

    @app.post("/api/v1/models/{model_id}/review")
    def review_model(model_id: UUID, s: DB, a: Identity, body: dto.ApprovalInput) -> dict[str, Any]:
        a.require("supervisor")
        if (now() - a.auth_time).total_seconds() > app.state.settings.approval_auth_age_seconds:
            raise Problem(403, "fresh_authentication_required")
        row = s.scalar(
            select(ModelVersion)
            .where(ModelVersion.id == model_id, ModelVersion.team == a.team)
            .with_for_update()
        )
        if not row:
            raise Problem(404, "not_found")
        if row.state != "REGISTERED" or body.expected_version != 1:
            raise Problem(409, "invalid_transition")
        if row.registered_by == a.id:
            raise Problem(403, "independent_approval_required")
        row.state = "APPROVED" if body.approved else "REJECTED"
        svc.audit(s, a, "model." + row.state.lower(), row.id)
        return views.fields(row, "id", "state")

    @app.get("/api/v1/models")
    def models(
        s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        q = select(ModelVersion).where(ModelVersion.team == a.team)
        if after:
            q = q.where(ModelVersion.id > after)
        rows = list(s.scalars(q.order_by(ModelVersion.id).limit(limit + 1)))
        return {
            "items": [
                views.fields(r, "id", "name", "digest", "state", "simulation", "metrics")
                for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            "configured_scorer": app.state.settings.scoring_mode,
        }

    @app.post("/api/v1/training-jobs", status_code=201)
    def training(s: DB, a: Identity) -> dict[str, Any]:
        a.require("supervisor")
        rows = s.execute(
            select(Transaction, Decision)
            .join(Case, Case.transaction_id == Transaction.id)
            .join(Decision, Decision.case_id == Case.id)
            .where(Case.team == a.team, Decision.disposition != "INCONCLUSIVE")
            .order_by(Transaction.occurred_at, Transaction.id)
            .limit(10001)
        ).all()
        if not rows:
            raise Problem(409, "reviewed_labels_required")
        if len(rows) > 10000:
            raise Problem(409, "snapshot_limit_exceeded")
        snapshot = [
            {
                "transaction_id": str(t.id),
                "amount_minor": t.amount_minor,
                "currency": t.currency,
                "channel": t.channel,
                "occurred_at": t.occurred_at.isoformat(),
                "context": t.context,
                "features": t.features,
                "decision_id": str(d.id),
                "label_available_at": d.created_at.isoformat(),
                "is_fraud": d.disposition == "CONFIRMED_FRAUD",
            }
            for t, d in rows
        ]
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        row = TrainingRequest(
            team=a.team, requested_by=a.id, snapshot=snapshot, snapshot_hash=digest
        )
        s.add(row)
        s.flush()
        svc.audit(s, a, "training.snapshot_created", row.id)
        return views.fields(row, "id", "state", "snapshot_hash", "created_at")

    @app.get("/api/v1/training-jobs/{job_id}")
    def training_status(job_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        row = s.scalar(
            select(TrainingRequest).where(
                TrainingRequest.id == job_id, TrainingRequest.team == a.team
            )
        )
        if not row:
            raise Problem(404, "not_found")
        return {
            **views.fields(row, "id", "state", "snapshot_hash", "model_id", "cancellation_reason"),
            "sample_size": len(row.snapshot),
        }

    @app.get("/api/v1/training-jobs/{job_id}/dataset")
    def dataset(job_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin", "supervisor")
        row = s.scalar(
            select(TrainingRequest).where(
                TrainingRequest.id == job_id, TrainingRequest.team == a.team
            )
        )
        if not row:
            raise Problem(404, "not_found")
        svc.audit(s, a, "training.dataset_exported", row.id)
        return {"snapshot_hash": row.snapshot_hash, "items": row.snapshot}

    @app.post("/api/v1/training-jobs/{job_id}/complete")
    def complete_training(
        job_id: UUID, body: dto.TrainingComplete, s: DB, a: Identity
    ) -> dict[str, Any]:
        a.require("admin")
        row = s.scalar(
            select(TrainingRequest)
            .where(TrainingRequest.id == job_id, TrainingRequest.team == a.team)
            .with_for_update()
        )
        model = s.scalar(
            select(ModelVersion).where(
                ModelVersion.id == body.model_id, ModelVersion.team == a.team
            )
        )
        if not row or not model:
            raise Problem(404, "not_found")
        if row.state != "READY" or row.snapshot_hash != body.snapshot_hash:
            raise Problem(409, "training_snapshot_conflict")
        row.state, row.model_id = "COMPLETED", model.id
        svc.audit(s, a, "training.result_registered", row.id)
        return views.fields(row, "id", "state", "model_id")

    @app.post("/api/v1/drift-reports", status_code=201)
    def drift(body: dto.DriftInput, s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        model = s.scalar(
            select(ModelVersion).where(
                ModelVersion.id == body.model_id, ModelVersion.team == a.team
            )
        )
        if not model:
            raise Problem(404, "not_found")
        if body.window_end > now():
            raise Problem(422, "future_window")
        row = DriftReport(team=a.team, **body.model_dump())
        s.add(row)
        s.flush()
        svc.audit(s, a, "drift.recorded", row.id)
        return views.fields(row, "id", "model_id", "statistic", "sample_size", "window_end")

    @app.get("/api/v1/drift-reports")
    def drift_reports(
        s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        a.require("admin", "supervisor", "auditor")
        q = select(DriftReport).where(DriftReport.team == a.team)
        if after:
            q = q.where(DriftReport.id > after)
        rows = list(s.scalars(q.order_by(DriftReport.id).limit(limit + 1)))
        return {
            "items": [
                views.fields(r, "id", "model_id", "statistic", "sample_size", "window_end")
                for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }
