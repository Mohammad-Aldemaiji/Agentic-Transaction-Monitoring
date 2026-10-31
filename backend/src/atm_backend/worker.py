"""Durable PostgreSQL job queue, transactional simulated effects, bounded retries.

SKIP LOCKED lets independent workers claim different jobs. Work and acknowledgement
commit atomically. A crash rolls both back. No external side effect may be inserted
into this transaction without adding an idempotent provider/reconciliation protocol.
"""

import json
import logging
import signal
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from atm_backend.adapters import Scorer, ScoringUnavailable
from atm_backend.db import database, failure_details
from atm_backend.e05 import E05Scorer
from atm_backend.features import build_features
from atm_backend.healthcheck import HEARTBEAT
from atm_backend.models import (
    AudioClip,
    Audit,
    BrowserSession,
    Call,
    Case,
    Execution,
    Investigation,
    Job,
    ModelVersion,
    PolicyVersion,
    Proposal,
    RateBucket,
    Transaction,
    now,
)
from atm_backend.scoring import RemoteScorer, configured_scorer
from atm_backend.services import INTRO, LIVE_CALL, event
from atm_backend.settings import Settings
from atm_backend.telemetry import Telemetry
from atm_backend.voice import introduction

logger = logging.getLogger(__name__)


def worker_audit(s: Session, team: str, kind: str, object_id: UUID) -> None:
    s.add(
        Audit(team=team, actor="worker", kind=kind, object_id=str(object_id), request_id="worker")
    )


def process(s: Session, job: Job, scorer: Scorer, settings: Settings | None = None) -> None:
    if job.kind == "score":
        transaction = s.get(Transaction, job.object_id)
        if not transaction:
            raise ValueError("Missing transaction")
        if transaction.status == "SCORED":
            return
        if isinstance(scorer, (RemoteScorer, E05Scorer)):
            approved = s.scalar(
                select(ModelVersion.id).where(
                    ModelVersion.team == transaction.team,
                    ModelVersion.name == scorer.settings.scoring_model_name,
                    ModelVersion.digest == scorer.settings.scoring_model_digest,
                    ModelVersion.state == "APPROVED",
                    ModelVersion.simulation.is_(False),
                )
            )
            if approved is None:
                raise ScoringUnavailable("Configured model requires independent approval")
        result = scorer.score(transaction, transaction.features or {})
        policy = s.scalar(
            select(PolicyVersion).where(
                PolicyVersion.team == transaction.team, PolicyVersion.state == "ACTIVE"
            )
        )
        # Without an approved policy, require review. A provider can always require
        # additional review; a threshold cannot override that safety decision.
        review = result.requires_review or policy is None or result.risk_score >= policy.threshold
        transaction.result = {
            **result.json(),
            "requires_review": review,
            "policy_version": policy.version if policy else None,
            "policy_threshold": policy.threshold if policy else None,
            "daily_review_capacity": policy.daily_review_capacity if policy else None,
            "severity": (
                "CRITICAL"
                if result.risk_score >= (policy.critical_threshold if policy else 0.85)
                else "HIGH"
                if result.risk_score >= (policy.high_threshold if policy else 0.65)
                else "MEDIUM"
                if result.risk_score >= (policy.medium_threshold if policy else 0.35)
                else "LOW"
            ),
            "severity_policy": f"risk-policy-v{policy.version}" if policy else "display-bands-v1",
            "severity_thresholds": {
                "medium": policy.medium_threshold if policy else 0.35,
                "high": policy.high_threshold if policy else 0.65,
                "critical": policy.critical_threshold if policy else 0.85,
            },
        }
        transaction.status = "SCORED"
        if review:
            case = s.scalar(select(Case).where(Case.transaction_id == transaction.id))
            if not case:
                case = Case(transaction_id=transaction.id, team=transaction.team)
                s.add(case)
                s.flush()
                event(s, case, "case.opened", simulation=result.simulation)
        worker_audit(s, transaction.team, "transaction.scored", transaction.id)
    elif job.kind == "voice":
        call = s.get(Call, job.object_id)
        if not call:
            raise ValueError("Missing call")
        if not s.scalar(select(AudioClip.id).where(AudioClip.call_id == call.id)):
            if settings and settings.piper_voice_dir:
                from atm_backend.local_voice import run_voice

                content = run_voice(
                    "speak",
                    settings.piper_voice_dir,
                    call.language,
                    INTRO[call.language].encode("utf-8"),
                )
            else:
                content = introduction(call.language, INTRO[call.language])
            s.add(AudioClip(call_id=call.id, content=content))
    elif job.kind == "execute":
        proposal = s.get(Proposal, job.object_id)
        if not proposal:
            raise ValueError("Approved proposal required")
        case = s.scalar(select(Case).where(Case.id == proposal.case_id).with_for_update())
        if case is None:
            raise ValueError("Proposal case missing")
        s.refresh(proposal)
        if proposal.state == "CANCELLED":
            return
        if proposal.state != "APPROVED":
            raise ValueError("Approved proposal required")
        if not s.scalar(select(Execution.id).where(Execution.proposal_id == proposal.id)):
            if case.state == "RESOLVED" or proposal.evidence_version != case.version:
                raise ValueError("Approved evidence no longer matches the open case")
            s.add(
                Execution(
                    proposal_id=proposal.id,
                    receipt={
                        "simulation": True,
                        "status": "SUCCEEDED",
                        "action": proposal.action,
                        "target": proposal.target,
                        "idempotency_key": str(proposal.id),
                        "provider": "database-simulation-v1",
                    },
                )
            )
            worker_audit(s, case.team, "action.simulated", proposal.id)
            event(s, case, "action.simulated", proposal_id=str(proposal.id))
    else:
        raise ValueError("Unsupported job")


def run_one(
    factory: sessionmaker[Session], scorer: Scorer, settings: Settings | None = None
) -> bool:
    with factory.begin() as s:
        job = s.scalar(
            select(Job)
            .where(Job.state == "PENDING", Job.available_at <= now())
            .order_by(Job.available_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return False
        job.attempts += 1
        try:
            # Commit the feature snapshot with the attempt, even when the provider fails.
            # Repeated attempts consume the same snapshot rather than moving history.
            if job.kind == "score":
                with s.begin_nested():
                    transaction = s.get(Transaction, job.object_id)
                    if transaction is not None and transaction.features is None:
                        transaction.features = build_features(s, transaction)
                        s.flush()
            with s.begin_nested():
                process(s, job, scorer, settings)
                s.flush()
        except Exception as exc:
            # Deliberately exclude provider messages/payloads and credentials from logs.
            logger.warning(
                json.dumps(
                    {"event": "job_attempt_failed", "job_id": str(job.id), **failure_details(exc)}
                )
            )
            job.error_code = "processing_unavailable"
            job.available_at = now() + timedelta(seconds=min(300, 2**job.attempts))
            if job.attempts >= 5:
                job.state = "FAILED"
                if job.kind == "score":
                    row = s.get(Transaction, job.object_id)
                    if row:
                        row.status = "FAILED"
                        worker_audit(s, row.team, "transaction.scoring_failed", row.id)
                        # An operational failure is reviewable, never a normal verdict.
                        case = s.scalar(select(Case).where(Case.transaction_id == row.id))
                        if case is None:
                            case = Case(transaction_id=row.id, team=row.team)
                            s.add(case)
                            s.flush()
                            event(
                                s,
                                case,
                                "case.opened",
                                reason="SCORING_UNAVAILABLE",
                                risk_score=None,
                            )
                            worker_audit(s, row.team, "case.manual_review_required", case.id)
        else:
            job.state = "DONE"
            job.error_code = None
    return True


def expire_waits(factory: sessionmaker[Session]) -> None:
    # Lock the case before calls, the same order as API mutations. Work in bounded batches.
    with factory.begin() as s:
        cases = list(
            s.scalars(
                select(Case)
                .where(
                    Case.id.in_(
                        select(Investigation.case_id)
                        .join(Call)
                        .where(Call.state.in_(LIVE_CALL), Call.expires_at <= now())
                    )
                )
                .order_by(Case.id)
                .with_for_update(skip_locked=True)
                .limit(100)
            )
        )
        for case in cases:
            calls = s.scalars(
                select(Call)
                .join(Investigation)
                .where(
                    Investigation.case_id == case.id,
                    Call.state.in_(LIVE_CALL),
                    Call.expires_at <= now(),
                )
            )
            for call in calls:
                call.state = "TIMED_OUT"
                call.version += 1
                case.state = "IN_REVIEW"
                case.version += 1
                event(s, case, "call.timed_out", call_id=str(call.id))
                worker_audit(s, case.team, "call.timed_out", call.id)
        expired_cases = s.scalars(
            select(Case)
            .where(
                Case.id.in_(
                    select(Proposal.case_id).where(
                        Proposal.state == "PENDING", Proposal.expires_at <= now()
                    )
                )
            )
            .order_by(Case.id)
            .with_for_update(skip_locked=True)
            .limit(100)
        )
        for case in expired_cases:
            for proposal in s.scalars(
                select(Proposal).where(
                    Proposal.case_id == case.id,
                    Proposal.state == "PENDING",
                    Proposal.expires_at <= now(),
                )
            ):
                proposal.state = "EXPIRED"
                proposal.version += 1
                event(s, case, "action.expired", proposal_id=str(proposal.id))
                worker_audit(s, case.team, "action.expired", proposal.id)
        s.execute(
            delete(BrowserSession).where(BrowserSession.expires_at < now() - timedelta(days=1))
        )
        s.execute(delete(RateBucket).where(RateBucket.minute < int(time.time() // 60) - 2))


def main() -> None:
    settings = Settings()
    engine, factory = database(settings)
    scorer = configured_scorer(settings)
    telemetry = Telemetry()
    if settings.metrics_enabled:
        telemetry.start(settings.metrics_port)
    running = True

    def stop(signum: int, frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    sweep_at = 0.0
    metrics_at = 0.0
    try:
        while running:
            try:
                if time.monotonic() >= sweep_at:
                    expire_waits(factory)
                    sweep_at = time.monotonic() + 5
                worked = run_one(factory, scorer, settings)
                if settings.metrics_enabled and time.monotonic() >= metrics_at:
                    with factory.begin() as session:
                        counts = {
                            state: count
                            for state, count in session.execute(
                                select(Job.state, func.count()).group_by(Job.state)
                            )
                        }
                        oldest = session.scalar(
                            select(func.min(Job.available_at)).where(
                                Job.state == "PENDING", Job.available_at <= now()
                            )
                        )
                    for state in ("PENDING", "DONE", "FAILED"):
                        telemetry.jobs.labels(state).set(counts.get(state, 0))
                    telemetry.queue_age.set(
                        max(0, (now() - oldest).total_seconds()) if oldest else 0
                    )
                    telemetry.queue_sample.set(time.time())
                    metrics_at = time.monotonic() + 15
                telemetry.worker_success.set(time.time())
                HEARTBEAT.write_text(str(time.monotonic()))
                if not worked:
                    time.sleep(0.5)
            except Exception as exc:
                telemetry.worker_errors.inc()
                logger.error(
                    json.dumps({"event": "worker_dependency_unavailable", **failure_details(exc)})
                )
                time.sleep(2)
    finally:
        telemetry.close()
        if isinstance(scorer, RemoteScorer):
            scorer.close()
        engine.dispose()


if __name__ == "__main__":
    main()
