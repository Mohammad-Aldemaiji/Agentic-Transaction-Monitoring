import hashlib
import json
import logging
import time
from collections.abc import Generator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atm_backend import contracts as dto
from atm_backend import queries, sessions, views
from atm_backend import services as svc
from atm_backend.db import database, failure_details
from atm_backend.models import (
    Audit,
    BrowserSession,
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
    RateBucket,
    Transaction,
    now,
)
from atm_backend.security import Actor, Problem, TokenVerifier
from atm_backend.settings import Settings
from atm_backend.telemetry import Telemetry

logger = logging.getLogger("atm.http")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate = False


class Boundary:
    def __init__(self, app: ASGIApp, limit: int, telemetry: Telemetry) -> None:
        self.app, self.limit = app, limit
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = str(uuid4())
        scope.setdefault("state", {})["request_id"] = rid
        body = bytearray()
        started = time.monotonic()
        status = 500

        async def secure_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                self.telemetry.response(
                    getattr(scope.get("route"), "path", "unmatched"),
                    scope.get("method", "OTHER"),
                    status,
                    time.monotonic() - started,
                )
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-request-id", rid.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await JSONResponse(
                    {"error": {"code": "body_too_large", "request_id": rid}}, status_code=413
                )(scope, receive, secure_send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, replay, secure_send)
        finally:
            # Route templates only; no tokens, query values, account IDs or request/response bodies.
            route = getattr(scope.get("route"), "path", "unmatched")
            logger.info(
                json.dumps(
                    {
                        "request_id": rid,
                        "route": route,
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                )
            )


def get_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.factory.begin() as session:
        yield session


DB = Annotated[Session, Depends(get_session, scope="function")]


def get_actor(
    request: Request, s: DB, authorization: Annotated[str | None, Header()] = None
) -> Actor:
    verifier: TokenVerifier = request.app.state.verifier
    if authorization:
        if not authorization.startswith("Bearer "):
            raise Problem(401, "authentication_required")
        claims = verifier.decode(authorization[7:])
        rate_key = hashlib.sha256((claims["iss"] + ":" + claims["sub"]).encode()).hexdigest()
    else:
        token = request.cookies.get(sessions.cookie_name(request), "")
        if not token or len(token) > 128:
            raise Problem(401, "authentication_required")
        session_key = sessions.digest(token)
    minute = int(time.time() // 60)
    with request.app.state.factory.begin() as rate_session:
        if not authorization:
            browser_session = rate_session.get(BrowserSession, session_key)
            if (
                browser_session is None
                or browser_session.revoked
                or min(browser_session.expires_at, browser_session.idle_expires_at) <= now()
            ):
                raise Problem(401, "session_expired")
            # Rotating cookies must not reset a user's allowance or separate it from bearer use.
            rate_key = hashlib.sha256(
                (browser_session.claims["iss"] + ":" + browser_session.claims["sub"]).encode()
            ).hexdigest()
        count = rate_session.scalar(
            insert(RateBucket)
            .values(key=rate_key, minute=minute, count=1)
            .on_conflict_do_update(
                index_elements=["key", "minute"], set_={"count": RateBucket.count + 1}
            )
            .returning(RateBucket.count)
        )
    if count > request.app.state.settings.requests_per_minute:
        raise Problem(429, "rate_limit_exceeded")
    if authorization:
        return verifier.from_claims(s, claims, request.state.request_id)
    return sessions.authenticate(request, s)


Identity = Annotated[Actor, Depends(get_actor)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    engine, factory = database(cfg)
    telemetry = Telemetry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if cfg.metrics_enabled:
            telemetry.start(cfg.metrics_port)
        try:
            yield
        finally:
            telemetry.close()
            engine.dispose()

    app = FastAPI(
        title="Transaction Monitoring API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings, app.state.factory, app.state.engine = cfg, factory, engine
    app.state.telemetry = telemetry
    app.state.verifier = TokenVerifier(cfg)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)
    app.add_middleware(Boundary, limit=cfg.request_limit_bytes, telemetry=telemetry)

    @app.exception_handler(Problem)
    async def problem(request: Request, exc: Problem) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        if exc.status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            {"error": {"code": exc.code, "request_id": request.state.request_id}},
            status_code=exc.status,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return await problem(request, Problem(422, "invalid_request"))

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        return await problem(request, Problem(409, "conflict"))

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            json.dumps(
                {
                    "event": "storage_unavailable",
                    "request_id": request.state.request_id,
                    **failure_details(exc),
                }
            )
        )
        return await problem(request, Problem(503, "storage_unavailable"))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unexpected_request_failure", extra={"request_id": request.state.request_id})
        return await problem(request, Problem(500, "internal_error"))

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(s: DB) -> dict[str, str]:
        if cfg.environment == "booth":
            s.execute(select(Principal.id).limit(1))
            return {"status": "ready"}
        if s.scalar(text("SELECT version_num FROM alembic_version")) != "0008":
            raise Problem(503, "schema_version_mismatch")
        if cfg.auth_provider == "supabase":
            # Exercise the managed-Auth boundary, not merely application tables.
            # No real identity/session is queried and this must always be false.
            if s.scalar(text("SELECT atm.provider_session_active(NULL::uuid)")) is not False:
                raise Problem(503, "authentication_boundary_unavailable")
        return {"status": "ready"}

    @app.post("/api/v1/auth/session")
    def start_session(request: Request, response: Response, s: DB, a: Identity) -> dict[str, Any]:
        result = sessions.establish(request, response, s, a)
        svc.audit(s, a, "session.created", a.id)
        return result

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, s: DB, a: Identity) -> None:
        if request.cookies.get(sessions.cookie_name(request)):
            sessions.authenticate(request, s)
        sessions.logout(request, response, s)
        svc.audit(s, a, "session.revoked", a.id)

    @app.get("/api/v1/me")
    def me(request: Request, a: Identity) -> dict[str, Any]:
        return {
            "id": a.id,
            "team": a.team,
            "role": a.role,
            "booth_mode": cfg.environment == "booth",
            "sample_traffic_enabled": request.app.state.settings.sample_traffic_enabled
            and a.role in ("analyst", "supervisor", "admin"),
        }

    @app.get("/api/v1/sample-traffic/evaluation")
    def sample_evaluation(request: Request, a: Identity) -> dict[str, Any]:
        from atm_backend.sample_traffic import evaluation

        return evaluation(a, request.app.state.settings)

    @app.post("/api/v1/sample-traffic", status_code=202)
    def sample_traffic(
        body: dto.SampleTrafficInput, request: Request, s: DB, a: Identity
    ) -> dict[str, int]:
        from atm_backend.sample_traffic import replay

        return {"accepted": replay(s, a, request.app.state.settings, body)}

    @app.get("/api/v1/openapi.json")
    def openapi(a: Identity) -> dict[str, Any]:
        return app.openapi()

    @app.post("/api/v1/transactions", status_code=202)
    def ingest(
        body: dto.TransactionInput,
        s: DB,
        a: Identity,
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
        ],
    ) -> dict[str, Any]:
        return views.transaction(svc.ingest(s, a, body, idempotency_key))

    @app.post("/api/v1/transactions/batch", status_code=202)
    def batch(body: dto.BatchInput, s: DB, a: Identity) -> dict[str, Any]:
        rows = svc.ingest_many(
            s,
            a,
            [
                (
                    dto.TransactionInput(**item.model_dump(exclude={"idempotency_key"})),
                    item.idempotency_key,
                )
                for item in body.items
            ],
        )
        return {"items": [views.transaction(row) for row in rows]}

    @app.get("/api/v1/transactions/{transaction_id}")
    def transaction(transaction_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        a.require("ingestion", *svc.READ_ROLES)
        row = s.scalar(
            select(Transaction).where(Transaction.id == transaction_id, Transaction.team == a.team)
        )
        if not row:
            raise Problem(404, "not_found")
        if a.role != "ingestion":
            case_id = s.scalar(select(Case.id).where(Case.transaction_id == row.id))
            if not case_id:
                raise Problem(404, "not_found")
            svc.case_access(s, a, case_id)
        return views.transaction(row)

    @app.get("/api/v1/alerts")
    @app.get("/api/v1/cases")
    def cases(
        s: DB,
        a: Identity,
        limit: PageSize = 50,
        after: UUID | None = None,
        state: queries.CaseState | None = None,
        assignment: Literal["all", "mine", "unassigned"] = "all",
        account_id: dto.Identifier | None = None,
        severity: queries.Severity | None = None,
        min_risk: Annotated[float | None, Query(ge=0, le=100)] = None,
        order: queries.QueueOrder = "id",
        created_from: AwareDatetime | None = None,
        created_to: AwareDatetime | None = None,
    ) -> dict[str, Any]:
        return queries.case_queue(
            s,
            a,
            limit=limit,
            after=after,
            state=state,
            assignment=assignment,
            account_id=account_id,
            severity=severity,
            min_risk=min_risk,
            order=order,
            created_from=created_from,
            created_to=created_to,
        )

    @app.get("/api/v1/cases/{case_id}")
    def case(case_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        row = svc.case_access(s, a, case_id)
        return views.case(row)

    @app.put("/api/v1/cases/{case_id}/assignment")
    def assignment(case_id: UUID, body: dto.AssignmentInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.case(svc.assign(s, a, case_id, body))

    @app.get("/api/v1/cases/{case_id}/evidence")
    def evidence(
        case_id: UUID, s: DB, a: Identity, limit: PageSize = 50, before: UUID | None = None
    ) -> dict[str, Any]:
        case = svc.case_access(s, a, case_id)
        transaction = s.get(Transaction, case.transaction_id)
        if transaction is None:
            raise Problem(409, "case_transaction_missing")
        # Every history query is bounded by the case transaction's time, never caller-supplied time.
        q = select(Transaction).where(
            Transaction.team == a.team,
            Transaction.account_id == transaction.account_id,
            Transaction.occurred_at <= transaction.occurred_at,
            Transaction.received_at <= case.created_at,
        )
        if before:
            q = q.where(Transaction.id < before)
        rows = list(s.scalars(q.order_by(Transaction.id.desc()).limit(limit + 1)))
        svc.audit(s, a, "evidence.read", case.id)
        return {
            "as_of": transaction.occurred_at,
            "transaction": views.transaction(transaction),
            "history": [views.evidence(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.post("/api/v1/cases/{case_id}/notes", status_code=201)
    def note(case_id: UUID, body: dto.NoteInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.fields(
            svc.add_note(s, a, case_id, body), "id", "text", "author_id", "created_at"
        )

    @app.get("/api/v1/cases/{case_id}/notes")
    def notes(
        case_id: UUID, s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        q = select(Note).where(Note.case_id == case_id)
        if after:
            q = q.where(Note.id > after)
        rows = list(s.scalars(q.order_by(Note.id).limit(limit + 1)))
        return {
            "items": [
                views.fields(r, "id", "text", "author_id", "created_at") for r in rows[:limit]
            ],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.post("/api/v1/cases/{case_id}/investigations")
    def investigate(
        case_id: UUID, body: dto.InvestigationInput, s: DB, a: Identity
    ) -> dict[str, Any]:
        return views.fields(
            svc.investigate(s, a, case_id, body), "id", "case_id", "state", "version"
        )

    @app.get("/api/v1/cases/{case_id}/investigations")
    def investigation(case_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        row = s.scalar(select(Investigation).where(Investigation.case_id == case_id))
        if not row:
            raise Problem(404, "not_found")
        return views.fields(row, "id", "case_id", "state", "version")

    @app.post("/api/v1/cases/{case_id}/calls", status_code=201)
    def create_call(case_id: UUID, body: dto.CallInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.call(svc.create_call(s, a, case_id, body, cfg))

    @app.get("/api/v1/calls")
    def calls(
        s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        a.require("participant")
        q = select(Call).where(Call.participant_id == a.id)
        if after:
            q = q.where(Call.id > after)
        rows = list(s.scalars(q.order_by(Call.id).limit(limit + 1)))
        return {
            "items": [views.call(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.get("/api/v1/calls/{call_id}")
    def call(call_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        row, _ = svc.call_access(s, a, call_id)
        return {**views.call(row), "local_transcription_enabled": bool(cfg.whisper_model_dir)}

    @app.post("/api/v1/calls/{call_id}/transition")
    def call_transition(
        call_id: UUID, body: dto.CallTransition, s: DB, a: Identity
    ) -> dict[str, Any]:
        return views.call(svc.transition_call(s, a, call_id, body))

    @app.post("/api/v1/calls/{call_id}/turns", status_code=201)
    def turn(call_id: UUID, body: dto.TurnInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.fields(svc.add_turn(s, a, call_id, body), "id", "sequence", "speaker", "text")

    @app.get("/api/v1/calls/{call_id}/turns")
    def turns(call_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        svc.call_access(s, a, call_id)
        svc.audit(s, a, "call.transcript_read", call_id)
        return {
            "items": [
                views.fields(r, "id", "sequence", "speaker", "text")
                for r in s.scalars(
                    select(CallTurn)
                    .where(CallTurn.call_id == call_id)
                    .order_by(CallTurn.sequence)
                    .limit(30)
                )
            ]
        }

    @app.post("/api/v1/cases/{case_id}/action-proposals", status_code=201)
    def propose(case_id: UUID, body: dto.ProposalInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.proposal(svc.propose(s, a, case_id, body))

    @app.get("/api/v1/cases/{case_id}/action-proposals")
    def proposals(
        case_id: UUID, s: DB, a: Identity, limit: PageSize = 50, after: UUID | None = None
    ) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        q = select(Proposal).where(Proposal.case_id == case_id)
        if after:
            q = q.where(Proposal.id > after)
        rows = list(s.scalars(q.order_by(Proposal.id).limit(limit + 1)))
        return {
            "items": [views.proposal(r) for r in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
        }

    @app.post("/api/v1/action-proposals/{proposal_id}/approval")
    def approve(proposal_id: UUID, body: dto.ApprovalInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.proposal(svc.approve(s, a, proposal_id, body, cfg))

    @app.post("/api/v1/action-proposals/{proposal_id}/cancel")
    def cancel_action(
        proposal_id: UUID, body: dto.CancellationInput, s: DB, a: Identity
    ) -> dict[str, Any]:
        return views.proposal(svc.cancel_action(s, a, proposal_id, body, cfg))

    @app.get("/api/v1/action-proposals/{proposal_id}/execution")
    def execution(proposal_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        proposal = s.get(Proposal, proposal_id)
        if not proposal:
            raise Problem(404, "not_found")
        svc.case_access(s, a, proposal.case_id)
        row = s.scalar(select(Execution).where(Execution.proposal_id == proposal.id))
        if row:
            return {"status": "SUCCEEDED", "receipt": row.receipt}
        if proposal.state != "APPROVED":
            return {
                "status": "AWAITING_APPROVAL" if proposal.state == "PENDING" else proposal.state
            }
        job = s.scalar(select(Job).where(Job.kind == "execute", Job.object_id == proposal.id))
        if not job or job.state == "DONE":
            raise Problem(503, "execution_record_inconsistent")
        return {"status": job.state, "attempts": job.attempts, "error_code": job.error_code}

    @app.post("/api/v1/cases/{case_id}/decisions", status_code=201)
    def decide(case_id: UUID, body: dto.DecisionInput, s: DB, a: Identity) -> dict[str, Any]:
        return views.fields(
            svc.decide(s, a, case_id, body),
            "id",
            "disposition",
            "reason",
            "reviewer_id",
            "created_at",
        )

    @app.get("/api/v1/cases/{case_id}/decisions")
    def decision(case_id: UUID, s: DB, a: Identity) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        row = s.scalar(select(Decision).where(Decision.case_id == case_id))
        if not row:
            raise Problem(404, "not_found")
        return views.fields(row, "id", "disposition", "reason", "reviewer_id", "created_at")

    @app.get("/api/v1/cases/{case_id}/events")
    def events(
        case_id: UUID,
        s: DB,
        a: Identity,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: PageSize = 50,
    ) -> dict[str, Any]:
        svc.case_access(s, a, case_id)
        rows = list(
            s.scalars(
                select(Event)
                .where(Event.case_id == case_id, Event.id > after)
                .order_by(Event.id)
                .limit(limit)
            )
        )
        return {
            "items": [views.fields(r, "id", "kind", "details", "created_at") for r in rows],
            "next_cursor": rows[-1].id if rows else after,
        }

    @app.get("/api/v1/audit/events")
    def audits(
        s: DB,
        a: Identity,
        after: Annotated[int, Query(ge=0)] = 0,
        through: Annotated[int | None, Query(ge=0)] = None,
        limit: PageSize = 50,
    ) -> dict[str, Any]:
        a.require("auditor")
        # A fixed upper cursor makes a paged export finite, even while reads are audited.
        watermark = (
            through
            if through is not None
            else s.scalar(select(func.max(Audit.id)).where(Audit.team == a.team)) or 0
        )
        rows = list(
            s.scalars(
                select(Audit)
                .where(Audit.team == a.team, Audit.id > after, Audit.id <= watermark)
                .order_by(Audit.id)
                .limit(limit + 1)
            )
        )
        svc.audit(s, a, "audit.read", str(after))
        return {
            "items": [
                views.fields(r, "id", "actor", "kind", "object_id", "request_id", "created_at")
                for r in rows[:limit]
            ],
            "next_cursor": rows[min(len(rows), limit) - 1].id if rows else after,
            "through": watermark,
            "has_more": len(rows) > limit,
        }

    @app.get("/api/v1/admin/jobs")
    def jobs(s: DB, a: Identity, limit: PageSize = 50) -> dict[str, Any]:
        a.require("admin")
        # Admins get operational job metadata only, never transaction payloads or transcripts.
        return {
            "items": [
                views.fields(r, "id", "kind", "state", "attempts", "error_code")
                for r in s.scalars(
                    select(Job)
                    .where(Job.state == "FAILED", Job.team == a.team)
                    .order_by(Job.id)
                    .limit(limit)
                )
            ]
        }

    @app.get("/api/v1/admin/metrics")
    def metrics(s: DB, a: Identity) -> dict[str, Any]:
        a.require("admin")
        oldest = s.scalar(
            select(func.min(Job.available_at)).where(Job.team == a.team, Job.state == "PENDING")
        )
        return {
            "oldest_ready_job_age_seconds": max(0, (now() - oldest).total_seconds())
            if oldest
            else 0,
            "jobs": {
                state: count
                for state, count in s.execute(
                    select(Job.state, func.count()).where(Job.team == a.team).group_by(Job.state)
                )
            },
        }

    from atm_backend.management import register

    register(app)
    from atm_backend.workflows import register as register_workflows

    register_workflows(app)

    @app.get("/api/v1/booth/config")
    def booth_config() -> dict[str, bool]:
        return {"enabled": cfg.environment == "booth"}

    if cfg.environment == "booth":
        from atm_backend.booth import register

        register(app)

    return app
