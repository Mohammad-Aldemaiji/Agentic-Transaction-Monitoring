"""Database tables: scored transactions, cases and their audit trail, account freezes,
messages to customers, and the analyst decisions that become training labels.

SQLite by default (data/monitoring.db); PostgreSQL by setting DATABASE_URL.
"""
import threading
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from src import config


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


def iso(dt):
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    """Every transaction the scoring service has seen, and what it decided."""
    __tablename__ = "transactions"
    txn_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    txn_time: Mapped[str] = mapped_column(String(40))          # the transaction's own timestamp
    amount_sar: Mapped[float] = mapped_column(Float)
    channel: Mapped[str] = mapped_column(String(40))
    counterparty: Mapped[str] = mapped_column(String(120), default="")
    # APPROVED, HELD (waiting on a case), RELEASED, BLOCKED, DECLINED (account frozen)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String(10), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(40))
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_id: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    # NEW -> ANALYSING -> AWAITING_APPROVAL -> CALLING -> ACTIONED -> CLOSED
    status: Mapped[str] = mapped_column(String(20), index=True, default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    probability: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(10))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    approved_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    call_language: Mapped[str] = mapped_column(String(16), default="English")
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)   # RELEASED, BLOCKED_AND_FROZEN
    final_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)  # FRAUD, LEGITIMATE
    closed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseEvent(Base):
    """The audit trail: who did what to a case, and when."""
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    actor: Mapped[str] = mapped_column(String(80))      # model, agent, analyst name, customer, system
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    changed_by: Mapped[str] = mapped_column(String(80), default="")
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Notification(Base):
    """Messages the bank sends the customer (shown on the demo phone)."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    text: Mapped[str] = mapped_column(Text)


class Label(Base):
    """An analyst's final decision on a case: the training label for the next model."""
    __tablename__ = "labels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_id: Mapped[str] = mapped_column(String(32), index=True)
    case_id: Mapped[int] = mapped_column(Integer)
    is_fraud: Mapped[int] = mapped_column(Integer)
    decided_by: Mapped[str] = mapped_column(String(80))
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    model: Mapped[str] = mapped_column(String(40))
    model_probability: Mapped[float] = mapped_column(Float)
    agent_disposition: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="ANALYST_DECISION")


_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(config.DATABASE_URL, connect_args=_args, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
write_lock = threading.RLock()  # SQLite allows one writer; keeps the worker threads in line


def init() -> None:
    if config.DATABASE_URL.startswith("sqlite"):
        config.ROOT.joinpath("data").mkdir(exist_ok=True)
        with engine.connect() as c:
            c.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
