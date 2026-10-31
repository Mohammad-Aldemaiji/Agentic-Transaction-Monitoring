"""Business rules shared by HTTP handlers and background work.

The caller owns the transaction: a state change, audit record and job commit together.
All case mutations lock the parent case first, then children in a consistent order.
"""

import hashlib
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from atm_backend import contracts as dto
from atm_backend.models import (
    Audit,
    Call,
    CallTurn,
    Case,
    Decision,
    Event,
    Execution,
    Investigation,
    Job,
    Note,
    Principal,
    Proposal,
    Transaction,
    now,
)
from atm_backend.security import Actor, Problem
from atm_backend.settings import Settings

REVIEW_ROLES = ("analyst", "supervisor")
READ_ROLES = (*REVIEW_ROLES, "auditor")
LIVE_CALL = ("RINGING", "ACTIVE")
HIGH_IMPACT = ("FREEZE_ACCOUNT", "FREEZE_DIGITAL_ACCESS", "BLOCK_CARD", "HOLD_TRANSFER")
INTRO = {
    "en": "This is Rasid bank verification. Press 1 if you made this payment. "
    "Press 2 if you did not authorize it. "
    "Never share your PIN, password or one-time code.",
    "ar": "هذه مكالمة تحقق من راصد. اضغط واحد إذا أجريت هذه العملية. اضغط اثنين إذا لم تسمح بها. "
    "لا تشارك الرقم السري أو كلمة المرور أو رمز التحقق.",
}


def audit(s: Session, a: Actor, kind: str, object_id: UUID | str) -> None:
    s.add(
        Audit(
            team=a.team,
            actor=str(a.id),
            kind=kind,
            object_id=str(object_id),
            request_id=a.request_id,
        )
    )


def event(s: Session, case: Case, kind: str, **details: Any) -> None:
    s.add(Event(case_id=case.id, kind=kind, details=details))


def version(actual: int, expected: int) -> None:
    if actual != expected:
        raise Problem(409, "stale_version")


def case_access(s: Session, a: Actor, case_id: UUID, *, write: bool = False) -> Case:
    a.require(*(REVIEW_ROLES if write else READ_ROLES))
    statement = select(Case).where(Case.id == case_id, Case.team == a.team)
    if a.role == "analyst":
        statement = statement.where((Case.assigned_to == a.id) | Case.assigned_to.is_(None))
    if write:
        statement = statement.with_for_update()
    case = s.scalar(statement)
    if not case:
        raise Problem(404, "not_found")
    return case


def open_case(case: Case) -> None:
    if case.state == "RESOLVED":
        raise Problem(409, "case_resolved")


def no_live_call(s: Session, case: Case) -> None:
    if s.scalar(
        select(Call.id)
        .join(Investigation)
        .where(
            Investigation.case_id == case.id,
            Call.state.in_(LIVE_CALL),
        )
        .limit(1)
    ):
        raise Problem(409, "call_in_progress")


def no_unfinished_action(s: Session, case: Case) -> None:
    if s.scalar(
        select(Proposal.id)
        .where(
            Proposal.case_id == case.id,
            Proposal.state == "APPROVED",
            ~select(Execution.id).where(Execution.proposal_id == Proposal.id).exists(),
        )
        .limit(1)
    ):
        raise Problem(409, "approved_action_pending")


def ingest(s: Session, a: Actor, payload: dto.TransactionInput, key: str) -> Transaction:
    return ingest_many(s, a, [(payload, key)])[0]


def ingest_many(
    s: Session, a: Actor, items: list[tuple[dto.TransactionInput, str]]
) -> list[Transaction]:
    """Accept a bounded batch atomically, with constant database round trips.

    Single and batch ingestion share the same ordered transaction locks. All
    conflicts are checked before staging inserts; retry results retain input order.
    """
    a.require("ingestion")
    return _ingest_many(s, a, items)


def _ingest_many(
    s: Session, a: Actor, items: list[tuple[dto.TransactionInput, str]]
) -> list[Transaction]:
    # Internal persistence; callers must authorize and constrain inputs first.
    if not 1 <= len(items) <= 100:
        raise Problem(422, "invalid_batch_size")
    locks = sorted(
        {
            lock
            for payload, key in items
            for lock in (f"{a.team}:id:{payload.external_id}", f"{a.team}:key:{key}")
        }
    )
    # The sorted subquery feeds the volatile locking function, so every caller
    # acquires overlapping locks in the same order, in one network round trip.
    s.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended(lock_key, 0)) "
            "FROM (SELECT unnest(CAST(:keys AS text[])) AS lock_key ORDER BY 1) ordered"
        ),
        {"keys": locks},
    ).all()
    rows = list(
        s.scalars(
            select(Transaction).where(
                Transaction.team == a.team,
                Transaction.idempotency_key.in_([key for _, key in items])
                | Transaction.external_id.in_([payload.external_id for payload, _ in items]),
            )
        )
    )
    by_key = {row.idempotency_key: row for row in rows}
    by_external = {row.external_id: row for row in rows}
    results: list[Transaction] = []
    new: list[Transaction] = []
    for payload, key in items:
        # Preserve the historical digest for absent optional context.
        context = payload.context.model_dump(exclude_none=True)
        canonical = payload.model_dump_json(
            exclude_none=True, exclude={"context"} if not context else set()
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        retry = by_key.get(key)
        natural = by_external.get(payload.external_id)
        if retry is not None or natural is not None:
            if retry is None or natural is not retry or retry.payload_hash != digest:
                raise Problem(409, "idempotency_conflict")
            results.append(retry)
            continue
        values = payload.model_dump()
        values["context"] = context
        row = Transaction(team=a.team, idempotency_key=key, payload_hash=digest, **values)
        by_key[key] = row
        by_external[payload.external_id] = row
        new.append(row)
        results.append(row)
    s.add_all(new)
    s.flush()
    for row in new:
        s.add(Job(team=a.team, kind="score", object_id=row.id))
        audit(s, a, "transaction.accepted", row.id)
    return results


def assign(s: Session, a: Actor, case_id: UUID, body: dto.AssignmentInput) -> Case:
    a.require("supervisor")
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    target = s.scalar(
        select(Principal).where(
            Principal.id == body.analyst_id,
            Principal.team == a.team,
            Principal.role == "analyst",
            Principal.active.is_(True),
        )
    )
    if not target:
        raise Problem(422, "invalid_assignee")
    case.assigned_to = target.id
    case.version += 1
    audit(s, a, "case.assigned", case.id)
    event(s, case, "case.assigned", analyst_id=str(target.id))
    return case


def add_note(s: Session, a: Actor, case_id: UUID, body: dto.NoteInput) -> Note:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    note = Note(case_id=case.id, author_id=a.id, text=body.text)
    s.add(note)
    case.version += 1
    audit(s, a, "note.added", case.id)
    event(s, case, "note.added")
    s.flush()
    return note


def investigate(s: Session, a: Actor, case_id: UUID, body: dto.InvestigationInput) -> Investigation:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    investigation = s.scalar(select(Investigation).where(Investigation.case_id == case.id))
    if body.operation == "start":
        if investigation:
            raise Problem(409, "investigation_exists")
        investigation = Investigation(case_id=case.id)
        s.add(investigation)
        case.state = "IN_REVIEW"
    else:
        if not investigation:
            raise Problem(404, "not_found")
        required = "ACTIVE" if body.operation == "cancel" else "CANCELLED"
        if investigation.state != required:
            raise Problem(409, "invalid_transition")
        no_live_call(s, case)
        investigation.state = "CANCELLED" if body.operation == "cancel" else "ACTIVE"
        investigation.version += 1
    case.version += 1
    audit(s, a, f"investigation.{body.operation}", case.id)
    event(s, case, f"investigation.{body.operation}")
    s.flush()
    return investigation


def create_call(
    s: Session, a: Actor, case_id: UUID, body: dto.CallInput, settings: Settings
) -> Call:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    investigation = s.scalar(
        select(Investigation).where(
            Investigation.case_id == case.id, Investigation.state == "ACTIVE"
        )
    )
    if not investigation:
        raise Problem(409, "active_investigation_required")
    no_live_call(s, case)
    participant = s.scalar(
        select(Principal).where(
            Principal.id == body.participant_id,
            Principal.team == a.team,
            Principal.role == "participant",
            Principal.active.is_(True),
        )
    )
    if not participant:
        raise Problem(422, "invalid_participant")
    call = Call(
        investigation_id=investigation.id,
        participant_id=participant.id,
        language=body.language,
        expires_at=now() + timedelta(seconds=settings.call_timeout_seconds),
    )
    s.add(call)
    case.state = "AWAITING_CUSTOMER"
    case.version += 1
    s.flush()
    audit(s, a, "call.created", call.id)
    event(s, case, "call.created", call_id=str(call.id))
    return call


def call_access(s: Session, a: Actor, call_id: UUID, *, write: bool = False) -> tuple[Call, Case]:
    a.require(*READ_ROLES, "participant")
    case_id = s.scalar(select(Investigation.case_id).join(Call).where(Call.id == call_id))
    if not case_id:
        raise Problem(404, "not_found")
    if a.role == "participant":
        case = s.scalar(
            select(Case).where(Case.id == case_id, Case.team == a.team).with_for_update()
        )
        if not case:
            raise Problem(404, "not_found")
    else:
        case = case_access(s, a, case_id, write=write)
    call = s.get(Call, call_id)
    if not call or (a.role == "participant" and call.participant_id != a.id):
        raise Problem(404, "not_found")
    return call, case


def transition_call(s: Session, a: Actor, call_id: UUID, body: dto.CallTransition) -> Call:
    call, case = call_access(s, a, call_id, write=True)
    version(call.version, body.expected_version)
    if call.expires_at <= now():
        raise Problem(409, "call_expired")
    if body.operation in ("answer", "decline"):
        a.require("participant")
        if call.state != "RINGING":
            raise Problem(409, "invalid_transition")
        call.state = "ACTIVE" if body.operation == "answer" else "DECLINED"
        if body.operation == "answer":
            s.add(Job(team=a.team, kind="voice", object_id=call.id))
            s.add(CallTurn(call_id=call.id, sequence=0, speaker="bank", text=INTRO[call.language]))
    else:
        if call.state not in LIVE_CALL:
            raise Problem(409, "invalid_transition")
        call.state = "ENDED"
    if call.state not in LIVE_CALL:
        case.state = "IN_REVIEW"
    call.version += 1
    case.version += 1
    audit(s, a, f"call.{body.operation}", call.id)
    event(s, case, f"call.{body.operation}", call_id=str(call.id))
    return call


def add_turn(s: Session, a: Actor, call_id: UUID, body: dto.TurnInput) -> CallTurn:
    a.require("participant", "analyst", "supervisor")
    call, case = call_access(s, a, call_id, write=True)
    version(call.version, body.expected_version)
    if call.state != "ACTIVE" or call.expires_at <= now():
        raise Problem(409, "call_not_active")
    latest = s.scalar(select(func.max(CallTurn.sequence)).where(CallTurn.call_id == call.id))
    sequence = 1 + (latest if latest is not None else -1)
    if sequence >= 30:
        raise Problem(409, "call_turn_limit")
    turn = CallTurn(
        call_id=call.id,
        sequence=sequence,
        speaker="customer" if a.role == "participant" else "bank",
        text=body.text,
    )
    s.add(turn)
    call.version += 1
    case.version += 1
    audit(s, a, "call.turn_recorded", call.id)
    event(s, case, "call.turn_recorded", call_id=str(call.id), sequence=sequence)
    s.flush()
    return turn


def propose(s: Session, a: Actor, case_id: UUID, body: dto.ProposalInput) -> Proposal:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    no_live_call(s, case)
    transaction = s.get(Transaction, case.transaction_id)
    if transaction is None:
        raise Problem(409, "case_transaction_missing")
    # No arbitrary target accepted from clients or a model. Bind to this case's transaction/account.
    target = str(transaction.id) if body.action == "HOLD_TRANSFER" else transaction.account_id
    if body.action == "HOLD_TRANSFER" and transaction.channel != "TRANSFER":
        raise Problem(422, "action_not_applicable")
    if body.action == "BLOCK_CARD":
        if transaction.channel not in ("CARD", "ATM") or not transaction.context.get("card_id"):
            raise Problem(422, "card_target_required")
        target = transaction.context["card_id"]
    proposal = Proposal(
        case_id=case.id,
        proposer_id=a.id,
        action=body.action,
        target=target,
        reason=body.reason,
        evidence_version=case.version,
        expires_at=now() + timedelta(minutes=10),
    )
    s.add(proposal)
    s.flush()
    audit(s, a, "action.proposed", proposal.id)
    event(s, case, "action.proposed", proposal_id=str(proposal.id))
    return proposal


def approve(
    s: Session, a: Actor, proposal_id: UUID, body: dto.ApprovalInput, settings: Settings
) -> Proposal:
    proposal = s.get(Proposal, proposal_id)
    if not proposal:
        raise Problem(404, "not_found")
    case = case_access(s, a, proposal.case_id, write=True)
    s.refresh(proposal)
    open_case(case)
    no_live_call(s, case)
    version(proposal.version, body.expected_version)
    if proposal.state != "PENDING" or proposal.expires_at <= now():
        raise Problem(409, "proposal_not_pending")
    if body.approved and proposal.evidence_version != case.version:
        raise Problem(409, "evidence_changed")
    if body.approved:
        no_unfinished_action(s, case)
    if (now() - a.auth_time).total_seconds() > settings.approval_auth_age_seconds:
        raise Problem(403, "fresh_authentication_required")
    if body.approved and proposal.action in HIGH_IMPACT:
        a.require("supervisor")
        if proposal.proposer_id == a.id:
            raise Problem(403, "independent_approval_required")
    proposal.state = "APPROVED" if body.approved else "REJECTED"
    proposal.approver_id = a.id
    proposal.version += 1
    if body.approved:
        s.add(Job(team=a.team, kind="execute", object_id=proposal.id))
    audit(s, a, f"action.{proposal.state.lower()}", proposal.id)
    event(s, case, f"action.{proposal.state.lower()}", proposal_id=str(proposal.id))
    return proposal


def cancel_action(
    s: Session, a: Actor, proposal_id: UUID, body: dto.CancellationInput, settings: Settings
) -> Proposal:
    proposal = s.get(Proposal, proposal_id)
    if not proposal:
        raise Problem(404, "not_found")
    case = case_access(s, a, proposal.case_id, write=True)
    s.refresh(proposal)
    version(proposal.version, body.expected_version)
    if proposal.state not in ("PENDING", "APPROVED"):
        raise Problem(409, "action_not_cancellable")
    if s.scalar(select(Execution.id).where(Execution.proposal_id == proposal.id)):
        raise Problem(409, "action_already_executed")
    if proposal.state == "APPROVED" or proposal.proposer_id != a.id:
        a.require("supervisor")
    if (now() - a.auth_time).total_seconds() > settings.approval_auth_age_seconds:
        raise Problem(403, "fresh_authentication_required")
    proposal.state = "CANCELLED"
    proposal.version += 1
    audit(s, a, "action.cancelled", proposal.id)
    event(s, case, "action.cancelled", proposal_id=str(proposal.id), reason=body.reason)
    # Do not lock/update the job here: workers lock jobs before the parent case.
    # A claimed or retried job will refresh the proposal under that case lock.
    return proposal


def decide(s: Session, a: Actor, case_id: UUID, body: dto.DecisionInput) -> Decision:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    no_live_call(s, case)
    if s.scalar(
        select(Proposal.id)
        .where(
            Proposal.case_id == case.id, Proposal.state == "PENDING", Proposal.expires_at > now()
        )
        .limit(1)
    ):
        raise Problem(409, "pending_proposals")
    row = Decision(
        case_id=case.id, reviewer_id=a.id, disposition=body.disposition, reason=body.reason
    )
    s.add(row)
    case.state = "RESOLVED"
    case.version += 1
    investigation = s.scalar(select(Investigation).where(Investigation.case_id == case.id))
    if investigation:
        investigation.state = "COMPLETED"
        investigation.version += 1
    audit(s, a, "case.decided", case.id)
    event(s, case, "case.decided", disposition=body.disposition)
    s.flush()
    return row


def record_finding(s: Session, a: Actor, case_id: UUID, body: dto.FindingInput) -> Event:
    case = case_access(s, a, case_id, write=True)
    open_case(case)
    version(case.version, body.expected_version)
    no_unfinished_action(s, case)
    investigation = s.scalar(
        select(Investigation).where(
            Investigation.case_id == case.id, Investigation.state == "ACTIVE"
        )
    )
    if not investigation:
        raise Problem(409, "active_investigation_required")
    transaction = s.get(Transaction, case.transaction_id)
    if transaction is None:
        raise Problem(409, "case_transaction_missing")
    evidence_ids = set(body.evidence_transaction_ids)
    if evidence_ids:
        allowed = set(
            s.scalars(
                select(Transaction.id).where(
                    Transaction.id.in_(evidence_ids),
                    Transaction.team == a.team,
                    Transaction.account_id == transaction.account_id,
                    Transaction.occurred_at <= transaction.occurred_at,
                    Transaction.received_at <= case.created_at,
                )
            )
        )
        if allowed != evidence_ids:
            raise Problem(422, "invalid_evidence_reference")
    case.version += 1
    row = Event(
        case_id=case.id,
        kind="investigation.finding",
        details={
            "author_id": str(a.id),
            "source": "human_review",
            "category": body.category,
            "summary": body.summary,
            "recommendation": body.recommendation,
            "evidence_transaction_ids": sorted(str(i) for i in evidence_ids),
            "evidence_version": case.version,
        },
    )
    s.add(row)
    audit(s, a, "investigation.finding_recorded", case.id)
    s.flush()
    return row


VERIFICATION_REPLIES = {
    "en": {
        "AUTHORIZED": "Thank you. Your response has been recorded for analyst review. "
        "This does not approve or release the transaction.",
        "NOT_AUTHORIZED": "Thank you. You reported that you did not authorize this transaction. "
        "An analyst will review protective action. "
        "Do not share any security codes.",
        "UNSURE": "Thank you. The transaction will remain under analyst review. "
        "Do not share any security codes.",
    },
    "ar": {
        "AUTHORIZED": "شكراً لك. تم تسجيل إجابتك لمراجعة المحلل. "
        "هذه الإجابة لا تعني الموافقة على العملية أو تنفيذها.",
        "NOT_AUTHORIZED": "شكراً لك. سجلنا أنك لم تأذن بهذه العملية. "
        "سيراجع المحلل الإجراء الوقائي. لا تشارك أي رموز أمنية.",
        "UNSURE": "شكراً لك. ستبقى العملية قيد مراجعة المحلل. لا تشارك أي رموز أمنية.",
    },
}


def verify_customer(s: Session, a: Actor, call_id: UUID, body: dto.VerificationInput) -> Call:
    a.require("participant")
    call, case = call_access(s, a, call_id, write=True)
    version(call.version, body.expected_version)
    if call.state != "ACTIVE" or call.expires_at <= now():
        raise Problem(409, "call_not_active")
    latest = s.scalar(select(func.max(CallTurn.sequence)).where(CallTurn.call_id == call.id))
    sequence = 1 + (latest if latest is not None else -1)
    if sequence > 28:
        raise Problem(409, "call_turn_limit")
    s.add_all(
        [
            CallTurn(call_id=call.id, sequence=sequence, speaker="customer", text=body.response),
            CallTurn(
                call_id=call.id,
                sequence=sequence + 1,
                speaker="bank",
                text=VERIFICATION_REPLIES[call.language][body.response],
            ),
        ]
    )
    call.state = "ENDED"
    call.version += 1
    case.state = "IN_REVIEW"
    case.version += 1
    event(
        s,
        case,
        "call.verification_recorded",
        call_id=str(call.id),
        response=body.response,
        participant_id=str(a.id),
    )
    audit(s, a, "call.verification_recorded", call.id)
    return call
