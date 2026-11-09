"""Scoped queue and dashboard read models with bounded, deterministic pagination."""

from datetime import UTC, date, datetime, timedelta
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


def traffic(s: Session, team: str) -> dict[str, Any]:
    """Team-wide transaction counters for the live dashboard cards."""
    current = now()
    day = current.replace(hour=0, minute=0, second=0, microsecond=0)
    hour = current - timedelta(hours=1)
    score = cast(Transaction.result["risk_score"].as_string(), Float)
    row = (
        s.execute(
            select(
                func.count().label("received"),
                func.count().filter(Transaction.received_at >= day).label("received_today"),
                func.count().filter(Transaction.received_at >= hour).label("received_last_hour"),
                func.count().filter(Transaction.status == "SCORED").label("scored"),
                func.count().filter(Transaction.status == "PENDING").label("pending"),
                func.count().filter(Transaction.status == "FAILED").label("failed"),
                func.count(Case.id).label("flagged"),
                func.count(Case.id).filter(Case.created_at >= hour).label("flagged_last_hour"),
                func.avg(score).filter(Transaction.status == "SCORED").label("average_score"),
                func.max(Transaction.received_at).label("last_received_at"),
            )
            .select_from(Transaction)
            .outerjoin(Case, Case.transaction_id == Transaction.id)
            .where(Transaction.team == team)
        )
        .mappings()
        .one()
    )
    received, average = row["received"] or 0, row["average_score"]
    # Ten-point score bands (display scale 0-100) for payments scored today (UTC).
    score_bins = [0] * 10
    for value in s.scalars(
        select(score).where(
            Transaction.team == team,
            Transaction.status == "SCORED",
            Transaction.received_at >= day,
            score.is_not(None),
        )
    ):
        score_bins[min(9, max(0, int(float(value) * 10)))] += 1
    return {
        "score_bins_today": score_bins,
        **{key: row[key] or 0 for key in row if key not in ("average_score", "last_received_at")},
        "flag_rate": round(row["flagged"] / received, 4) if received else None,
        # Display scale (model output x 100), the same uncalibrated score shown elsewhere.
        "average_risk_score": round(float(average) * 100, 2) if average is not None else None,
        "last_received_at": row["last_received_at"],
    }


TREND_DAYS = 14
DECISION_DAYS = 7
BANDS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNSCORED")


def utc_day(value: datetime) -> date:
    return (value.astimezone(UTC) if value.tzinfo else value).date()


def trends(s: Session, ids: Any, day: datetime) -> dict[str, Any]:
    """Daily chart series for the overview, limited to the actor's visible cases."""
    start = day - timedelta(days=TREND_DAYS - 1)
    days = [(start + timedelta(days=i)).date() for i in range(TREND_DAYS)]
    flagged = {d: dict.fromkeys(BANDS, 0) for d in days}
    rows = s.execute(
        select(Case.created_at, severity_expression())
        .select_from(Case)
        .join(Transaction, Transaction.id == Case.transaction_id)
        .where(Case.id.in_(ids), Case.created_at >= start)
    ).all()
    for created, band in rows:
        bucket = flagged.get(utc_day(created))
        if bucket is not None and band in bucket:
            bucket[band] += 1
    recent = days[-DECISION_DAYS:]
    decided = dict.fromkeys(recent, 0)
    hours: dict[date, list[float]] = {d: [] for d in recent}
    for decided_at, opened in s.execute(
        select(Decision.created_at, Case.created_at)
        .select_from(Decision)
        .join(Case, Case.id == Decision.case_id)
        .where(
            Decision.case_id.in_(ids),
            Decision.created_at >= day - timedelta(days=DECISION_DAYS - 1),
        )
    ).all():
        key = utc_day(decided_at)
        if key in decided:
            decided[key] += 1
            hours[key].append(max(0.0, (decided_at - opened).total_seconds() / 3600))
    return {
        "days": [d.isoformat() for d in days],
        "flagged_by_day": [flagged[d] for d in days],
        "decided_by_day": [decided[d] for d in recent],
        # Mean hours from case opened to final decision, per day; null when nothing was decided.
        "decision_hours_by_day": [
            round(sum(hours[d]) / len(hours[d]), 2) if hours[d] else None for d in recent
        ],
    }


def dashboard(s: Session, a: Actor, include_traffic: bool = False) -> dict[str, Any]:
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
    decided_today = (
        s.scalar(
            select(func.count())
            .select_from(Decision)
            .where(Decision.case_id.in_(ids), Decision.created_at >= day)
        )
        or 0
    )
    return {
        "review_capacity": capacity,
        "transactions": traffic(s, a.team) if include_traffic else None,
        "trends": trends(s, ids, day),
        "review_threshold": round(policy.threshold * 100, 2) if policy else None,
        "decided_today": decided_today,
        "generated_at": now(),
        "cases": counts,
        "open_severity": bands,
        "decisions": decisions,
        "oldest_open_at": oldest,
        "scope": "authorized_cases",
        "severity_policy": "recorded-per-transaction",
    }
