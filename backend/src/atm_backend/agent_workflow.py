"""Authorization, bounded evidence and optimistic concurrency for local analysis."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atm_backend import services as svc
from atm_backend.local_agent import Analysis
from atm_backend.models import Call, CallTurn, Case, Event, Investigation, Principal, Transaction
from atm_backend.security import Actor, Problem


def check_case(s: Session, a: Actor, case_id: UUID, expected_version: int) -> Case:
    principal = s.get(Principal, a.id)
    if (
        not principal
        or not principal.active
        or (principal.team, principal.role) != (a.team, a.role)
    ):
        raise Problem(403, "permission_denied")
    case = svc.case_access(s, a, case_id, write=True)
    svc.open_case(case)
    svc.version(case.version, expected_version)
    svc.no_unfinished_action(s, case)
    svc.no_live_call(s, case)
    if not s.scalar(
        select(Investigation.id).where(
            Investigation.case_id == case.id, Investigation.state == "ACTIVE"
        )
    ):
        raise Problem(409, "active_investigation_required")
    return case


def collect(s: Session, a: Actor, case_id: UUID, expected_version: int) -> dict[str, Any]:
    case = check_case(s, a, case_id, expected_version)
    transaction = s.get(Transaction, case.transaction_id)
    if transaction is None:
        raise Problem(409, "case_transaction_missing")
    history = list(
        s.scalars(
            select(Transaction)
            .where(
                Transaction.team == a.team,
                Transaction.account_id == transaction.account_id,
                Transaction.id != transaction.id,
                Transaction.occurred_at <= transaction.occurred_at,
                Transaction.occurred_at >= transaction.occurred_at - timedelta(days=90),
                Transaction.received_at <= case.created_at,
            )
            .order_by(Transaction.occurred_at.desc(), Transaction.id)
            .limit(19)
        )
    )
    calls = list(
        s.scalars(
            select(Call)
            .join(Investigation)
            .where(Investigation.case_id == case.id)
            .order_by(Call.created_at.desc(), Call.id)
            .limit(4)
        )
    )
    verification = []
    text_budget = 1000
    for call in calls[:3]:
        turns = list(
            s.scalars(
                select(CallTurn)
                .where(CallTurn.call_id == call.id)
                .order_by(CallTurn.sequence.desc())
                .limit(7)
            )
        )
        transcript = []
        truncated = len(turns) > 6
        for turn in turns[:6]:
            excerpt = turn.text[: min(300, text_budget)]
            truncated |= len(excerpt) < len(turn.text)
            text_budget -= len(excerpt)
            if excerpt:
                transcript.append({"speaker": turn.speaker, "text": excerpt})
        verification.append(
            {
                "call_id": str(call.id),
                "state": call.state,
                "language": call.language,
                "transcript": list(reversed(transcript)),
                "transcript_truncated": truncated,
            }
        )
    # Explicit fields: never serialize ORM objects, principals, labels or credentials.
    evidence = {
        "case_id": str(case.id),
        "evidence_version": case.version,
        "alert_transaction_id": str(transaction.id),
        "history_limit": 19,
        "history_may_be_truncated": len(history) == 19,
        "score": transaction.result,
        "customer_verification": verification,
        "verification_history_truncated": len(calls) > 3,
        "account_features": transaction.features,
        "transactions": [
            {
                "id": str(t.id),
                "amount_minor": t.amount_minor,
                "currency": t.currency,
                "occurred_at": t.occurred_at.isoformat(),
                "channel": t.channel,
                "context": t.context,
            }
            for t in [transaction, *history]
        ],
    }
    svc.audit(s, a, "agent.analysis_requested", case.id)
    return evidence


def save(
    s: Session, a: Actor, case_id: UUID, evidence: dict[str, Any], result: Analysis, model: str
) -> Event:
    case = check_case(s, a, case_id, evidence["evidence_version"])
    allowed = {row["id"] for row in evidence["transactions"]}
    if not set(result.evidence_transaction_ids) <= allowed:
        raise Problem(422, "invalid_evidence_reference")
    mapping = {
        "RELEASE": "NO_ACTION",
        "VERIFY_WITH_CUSTOMER": "CONTACT_CUSTOMER",
        "BLOCK_AND_FREEZE": "PROPOSE_ACTION",
    }
    case.version += 1
    row = Event(
        case_id=case.id,
        kind="investigation.finding",
        details={
            "source": "local_llm",
            "model": model,
            "advisory_only": True,
            "author_id": str(a.id),
            "category": "TRANSACTION_PATTERN",
            "summary": result.summary,
            "recommendation": mapping[result.recommended_action],
            "evidence_transaction_ids": result.evidence_transaction_ids,
            "analysed_evidence_version": evidence["evidence_version"],
            "evidence_version": case.version,
            "analysis": result.model_dump(),
        },
    )
    s.add(row)
    svc.audit(s, a, "agent.analysis_recorded", case.id)
    s.flush()
    return row
