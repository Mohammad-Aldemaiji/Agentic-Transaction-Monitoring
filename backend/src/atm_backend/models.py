"""Persistent state. Financial values use integer minor units; all times are UTC."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema="atm")


class Principal(Base):
    __tablename__ = "principals"
    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        CheckConstraint(
            "role IN ('analyst','supervisor','auditor','admin','ingestion','participant')"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    issuer: Mapped[str] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(200))
    team: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("team", "external_id"),
        UniqueConstraint("team", "idempotency_key"),
        CheckConstraint("amount_minor > 0 AND amount_minor <= 1000000000000"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'"),
        CheckConstraint("status IN ('PENDING','SCORED','FAILED')"),
        Index("ix_transaction_history", "team", "account_id", "occurred_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(100))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(100))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("state IN ('OPEN','IN_REVIEW','AWAITING_CUSTOMER','RESOLVED')"),
        CheckConstraint("version > 0"),
        Index("ix_cases_team_queue", "team", "id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(ForeignKey("transactions.id"), unique=True)
    team: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), default="OPEN")
    version: Mapped[int] = mapped_column(Integer, default=1)
    assigned_to: Mapped[UUID | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Investigation(Base):
    __tablename__ = "investigations"
    __table_args__ = (CheckConstraint("state IN ('ACTIVE','CANCELLED','COMPLETED')"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("state IN ('RINGING','ACTIVE','ENDED','DECLINED','TIMED_OUT','CANCELLED')"),
        CheckConstraint("language IN ('en','ar')"),
        Index("ix_calls_investigation", "investigation_id"),
        Index(
            "ix_calls_expiry",
            "expires_at",
            postgresql_where=text("state IN ('RINGING','ACTIVE')"),
        ),
        Index(
            "uq_live_call",
            "investigation_id",
            unique=True,
            postgresql_where=text("state IN ('RINGING','ACTIVE')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    investigation_id: Mapped[UUID] = mapped_column(ForeignKey("investigations.id"))
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="RINGING")
    language: Mapped[str] = mapped_column(String(2))
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CallTurn(Base):
    __tablename__ = "call_turns"
    __table_args__ = (UniqueConstraint("call_id", "sequence"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint("state IN ('PENDING','APPROVED','REJECTED','EXPIRED','CANCELLED')"),
        CheckConstraint(
            "action IN ('BLOCK_CARD','HOLD_TRANSFER','FREEZE_DIGITAL_ACCESS',"
            "'FREEZE_ACCOUNT','STEP_UP_AUTH','NO_ACTION')"
        ),
        CheckConstraint("expires_at > created_at"),
        Index("ix_proposal_case", "case_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    proposer_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    approver_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(2000))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(Integer, default=1)
    evidence_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(ForeignKey("proposals.id"), unique=True)
    # This adapter only records a simulated receipt; it has no bank connection.
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint("disposition IN ('CONFIRMED_FRAUD','LEGITIMATE','INCONCLUSIVE')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), unique=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    disposition: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    text: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = "case_events"
    __table_args__ = (Index("ix_case_events_cursor", "case_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("cases.id"))
    kind: Mapped[str] = mapped_column(String(50))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_team_cursor", "team", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    team: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(60))
    object_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("kind", "object_id"),
        CheckConstraint("state IN ('PENDING','DONE','FAILED')"),
        Index(
            "ix_jobs_available",
            "available_at",
            "id",
            postgresql_where=text("state = 'PENDING'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    object_id: Mapped[UUID] = mapped_column()
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    minute: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    claims: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AudioClip(Base):
    __tablename__ = "audio_clips"
    __table_args__ = (CheckConstraint("octet_length(content) <= 1048576"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id"), unique=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PolicyVersion(Base):
    __tablename__ = "risk_policies"
    __table_args__ = (
        UniqueConstraint("team", "version"),
        CheckConstraint("threshold >= 0 AND threshold <= 1"),
        CheckConstraint("daily_review_capacity BETWEEN 1 AND 100000"),
        CheckConstraint(
            "0 <= medium_threshold AND medium_threshold < high_threshold "
            "AND high_threshold < critical_threshold AND critical_threshold <= 1"
        ),
        Index(
            "uq_policy_active",
            "team",
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[float] = mapped_column(Float)
    daily_review_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    medium_threshold: Mapped[float] = mapped_column(Float, default=0.35, server_default="0.35")
    high_threshold: Mapped[float] = mapped_column(Float, default=0.65, server_default="0.65")
    critical_threshold: Mapped[float] = mapped_column(Float, default=0.85, server_default="0.85")
    state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    approved_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("principals.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("team", "name"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))
    simulation: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="REGISTERED")
    registered_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TrainingRequest(Base):
    __tablename__ = "training_requests"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64), index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="READY")
    cancellation_reason: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Immutable reviewed-label snapshot, excludes free-form notes and call transcripts.
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DriftReport(Base):
    __tablename__ = "drift_reports"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    team: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[UUID] = mapped_column(ForeignKey("model_versions.id"), index=True)
    statistic: Mapped[float] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
