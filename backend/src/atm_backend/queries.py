"""Scoped queue and dashboard read models with bounded, deterministic pagination."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Float, Select, and_, cast, func, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.orm import Session

from atm_backend import services as svc
from atm_backend import views
from atm_backend.models import Case, Decision, PolicyVersion, Transaction, now
from atm_backend.security import Actor, Problem

CaseState = Literal["OPEN", "IN_REVIEW", "AWAITING_CUSTOMER", "RESOLVED"]
Severity = Literal["UNSCORED", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
QueueOrder = Literal["id", "oldest", "newest", "risk"]


def visible_cases(a: Actor) -> Select[tuple[Case]]:
    a.require(*svc.READ_ROLES)
    q = select(Case).where(Case.team == a.team)
    if a.role == "analyst":
        q = q.where(or_(Case.assigned_to == a.id, Case.assigned_to.is_(None)))
    return q


def risk_expression() -> Any:
    return cast(Transaction.result["risk_score"].as_string(), Float) * 100


def severity_expression() -> Any:
    risk = risk_expression()
    return func.coalesce(
        Transaction.result["severity"].as_string(),
        sql_case(
            (risk.is_(None), "UNSCORED"),
            (risk >= 85, "CRITICAL"),
            (risk >= 65, "HIGH"),
            (risk >= 35, "MEDIUM"),
            else_="LOW",
        ),
    )


def queue_item(c: Case, t: Transaction) -> dict[str, Any]:
    score = (t.result or {}).get("risk_score")
    risk = score * 100 if score is not None else None
    severity = (
        "UNSCORED"
        if risk is None
        else "CRITICAL"
        if risk >= 85
        else "HIGH"
        if risk >= 65
        else "MEDIUM"
        if risk >= 35
        else "LOW"
    )
    return {
        **views.case(c),
        "account_id": t.account_id,
        "external_id": t.external_id,
        "amount_minor": t.amount_minor,
        "currency": t.currency,
        "channel": t.channel,
        "processing_status": t.status,
        "risk_score": risk,
        "risk_scale": "0-100",
        "severity": (t.result or {}).get("severity", severity),
        "severity_policy": (t.result or {}).get("severity_policy", "display-bands-v1"),
        "review_reason": "SCORING_UNAVAILABLE" if t.status == "FAILED" else "SCORING_REVIEW",
        "simulation": (t.result or {}).get("simulation"),
    }


def case_queue(
    s: Session,
    a: Actor,
    *,
    limit: int,
    after: UUID | None,
    state: CaseState | None = None,
    assignment: Literal["all", "mine", "unassigned"] = "all",
    account_id: str | None = None,
    severity: Severity | None = None,
    min_risk: float | None = None,
    order: QueueOrder = "id",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> dict[str, Any]:
    if created_from and created_to and created_from > created_to:
        raise Problem(422, "invalid_time_range")
    q = (
        visible_cases(a)
        .add_columns(Transaction)
        .join(Transaction, Transaction.id == Case.transaction_id)
    )
    if state:
        q = q.where(Case.state == state)
    if assignment == "mine":
        q = q.where(Case.assigned_to == a.id)
    elif assignment == "unassigned":
        q = q.where(Case.assigned_to.is_(None))
    if account_id:
        q = q.where(Transaction.account_id == account_id)
    if severity:
        q = q.where(severity_expression() == severity)
    if min_risk is not None:
        q = q.where(risk_expression() >= min_risk)
    if created_from:
        q = q.where(Case.created_at >= created_from)
    if created_to:
        q = q.where(Case.created_at <= created_to)
    key = func.coalesce(risk_expression(), -1) if order == "risk" else Case.created_at
    descending = order in ("risk", "newest")
    if after:
        cursor = s.execute(
            visible_cases(a)
            .add_columns(Transaction)
            .join(Transaction, Transaction.id == Case.transaction_id)
            .where(Case.id == after)
        ).first()
        if cursor is None:
            raise Problem(422, "invalid_cursor")
        c, t = cursor
        value = (t.result or {}).get("risk_score")
        key_value = (value * 100 if value is not None else -1) if order == "risk" else c.created_at
        if order == "id":
            q = q.where(Case.id > after)
        else:
            q = q.where(
                or_(
                    key < key_value if descending else key > key_value,
                    and_(key == key_value, Case.id > after),
                )
            )
    q = (
        q.order_by(Case.id)
        if order == "id"
        else q.order_by(key.desc() if descending else key.asc(), Case.id)
    )
    rows = s.execute(q.limit(limit + 1)).all()
    return {
        "items": [queue_item(c, t) for c, t in rows[:limit]],
        "next_cursor": str(rows[limit - 1][0].id) if len(rows) > limit else None,
        "order": order,
    }


def dashboard(s: Session, a: Actor) -> dict[str, Any]:
    ids = visible_cases(a).with_only_columns(Case.id)
    counts = dict(
        s.execute(select(Case.state, func.count()).where(Case.id.in_(ids)).group_by(Case.state))
        .tuples()
        .all()
    )
    severity = severity_expression()
    bands = dict(
        s.execute(
            select(severity, func.count())
            .select_from(Case)
            .join(Transaction)
            .where(Case.id.in_(ids), Case.state != "RESOLVED")
            .group_by(severity)
        )
        .tuples()
        .all()
    )
    decisions = dict(
        s.execute(
            select(Decision.disposition, func.count())
            .where(Decision.case_id.in_(ids))
            .group_by(Decision.disposition)
        )
        .tuples()
        .all()
    )
    oldest = s.scalar(
        select(func.min(Case.created_at)).where(Case.id.in_(ids), Case.state != "RESOLVED")
    )
    policy = s.scalar(
        select(PolicyVersion).where(PolicyVersion.team == a.team, PolicyVersion.state == "ACTIVE")
    )
    day = now().replace(hour=0, minute=0, second=0, microsecond=0)
    # Capacity is an operational target, never a cap that silently hides alerts.
    # Only team-wide reviewers receive team workload counts.
    capacity = None
    if a.role in ("supervisor", "auditor"):
        opened = (
            s.scalar(
                select(func.count())
                .select_from(Case)
                .where(Case.team == a.team, Case.created_at >= day)
            )
            or 0
        )
        completed = (
            s.scalar(
                select(func.count())
                .select_from(Decision)
                .join(Case)
                .where(Case.team == a.team, Decision.created_at >= day)
            )
            or 0
        )
        target = policy.daily_review_capacity if policy else None
        capacity = {
            "day_start_utc": day,
            "target": target,
            "opened_today": opened,
            "completed_today": completed,
            "over_target": opened > target if target is not None else None,
            "remaining_target": max(0, target - completed) if target is not None else None,
            "policy_version": policy.version if policy else None,
            "suppresses_alerts": False,
        }
    return {
        "review_capacity": capacity,
        "cases": counts,
        "open_severity": bands,
        "decisions": decisions,
        "oldest_open_at": oldest,
        "scope": "authorized_cases",
        "severity_policy": "recorded-per-transaction",
    }
