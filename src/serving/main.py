"""HTTP API for the dashboard, the transaction feed and the demo phone.

    uvicorn src.serving.main:app --port 8000        (or: python -m src.serving)

If dashboard/dist exists (npm run build), the dashboard is served from / as well.
"""
import csv
import io
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from src import config
from src.agent import investigator, llm, voice
from src.serving import db
from src.serving.db import Account, Case, CaseEvent, Label, Notification, Transaction, iso
from src.serving.service import OPEN, Conflict, Monitor

M: Monitor = None


@asynccontextmanager
async def lifespan(app):
    global M
    M = Monitor()
    print(f"Ready: {len(M.history.df):,} transactions in history ({config.DATA_FILE.name}), "
          f"model {M.model.name}, agent {config.AGENT_MODEL} at {config.OLLAMA_HOST}")
    yield


app = FastAPI(title="Agentic Transaction Monitoring", lifespan=lifespan)


def _errors(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except Conflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


class Analyst(BaseModel):
    analyst: str = Field(min_length=1, max_length=80)


# ------------------------------------------------------------------ health and stats
@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.status(), "voice": voice.tts_status(),
            "data_file": config.DATA_FILE.name, "transactions_in_history": len(M.history.df),
            "database": config.DATABASE_URL.split("://")[0]}


@app.get("/api/stats")
def stats():
    return M.stats()


# ------------------------------------------------------------------ transactions
class TxnIn(BaseModel):
    txn_id: str | None = None
    record: dict | None = None


@app.post("/api/transactions")
def ingest(body: TxnIn):
    """Score one transaction. Send {"txn_id": ...} to replay one from the history
    store, or {"record": {...}} with every dataset column for a new one."""
    if not body.txn_id and not body.record:
        raise HTTPException(422, "Send txn_id or record.")
    return _errors(M.ingest, body.txn_id, body.record)


@app.get("/api/transactions")
def transactions(limit: int = 100, decision: str | None = None):
    with db.Session() as s:
        q = select(Transaction).order_by(Transaction.scored_at.desc(), Transaction.txn_time.desc()).limit(min(limit, 500))
        if decision:
            q = q.where(Transaction.decision == decision)
        return [M._txn_dict(t) for t in s.scalars(q)]


# ------------------------------------------------------------------ cases
def _case_row(c: Case, t: Transaction) -> dict:
    a = c.analysis or {}
    return {"id": c.id, "txn_id": c.txn_id, "account_id": c.account_id, "status": c.status,
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at),
            "probability": c.probability, "risk_score": c.risk_score, "band": c.band,
            "amount_sar": t.amount_sar if t else None, "channel": t.channel if t else None,
            "counterparty": t.counterparty if t else None, "txn_time": t.txn_time if t else None,
            "recommended_action": c.recommended_action, "disposition": a.get("disposition"),
            "agent_error": bool(c.agent_error), "approved_action": c.approved_action,
            "outcome": c.outcome, "final_decision": c.final_decision}


@app.get("/api/cases")
def cases(status: str = "open", band: str | None = None, limit: int = 200):
    with db.Session() as s:
        q = select(Case, Transaction).join(Transaction, Transaction.txn_id == Case.txn_id, isouter=True)
        if status == "open":
            q = q.where(Case.status.in_(OPEN))
        elif status != "all":
            q = q.where(Case.status == status.upper())
        if band:
            q = q.where(Case.band == band.upper())
        q = q.order_by(Case.created_at.desc()).limit(min(limit, 1000))
        return [_case_row(c, t) for c, t in s.execute(q).all()]


@app.get("/api/cases/{case_id}")
def case_detail(case_id: int):
    with db.Session() as s:
        c = s.get(Case, case_id)
        if not c:
            raise HTTPException(404, "Case not found.")
        t = s.get(Transaction, c.txn_id)
        events = s.scalars(select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)).all()
        acct = s.get(Account, c.account_id)
        out = _case_row(c, t)
        out.update({
            "reasons": c.reasons, "analysis": c.analysis, "agent_error": c.agent_error,
            "approved_by": c.approved_by, "approved_at": iso(c.approved_at), "call_language": c.call_language,
            "verification": c.verification, "closed_by": c.closed_by, "closed_at": iso(c.closed_at),
            "close_note": c.close_note, "latency_ms": t.latency_ms if t else None,
            "account_frozen": bool(acct and acct.frozen),
            "events": [{"id": e.id, "at": iso(e.at), "actor": e.actor, "kind": e.kind, "text": e.text,
                        "data": e.data} for e in events],
        })
    if M.history.has(c.txn_id):
        tools = investigator.CaseTools(M.history, c.txn_id)
        out["transaction"] = json.loads(tools.get_transaction(c.txn_id))
        hist = tools.get_account_history(c.account_id)
        out["account_history"] = json.loads(hist) if hist.startswith("{") else None
        if c.status == "CLOSED":  # synthetic data only: lets the demo show whether the call was right
            out["answer_key"] = M.history.truth(c.txn_id)
    return out


class Approve(Analyst):
    action: str
    language: str = "English"


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: int, body: Approve):
    lang = body.language if body.language in voice.LANGUAGES else "English"
    _errors(M.approve, case_id, body.action, body.analyst.strip(), lang)
    return {"ok": True}


@app.post("/api/cases/{case_id}/reanalyse")
def reanalyse(case_id: int, body: Analyst):
    _errors(M.reanalyse, case_id, body.analyst.strip())
    return {"ok": True}


class Close(Analyst):
    decision: str
    note: str = ""


@app.post("/api/cases/{case_id}/close")
def close(case_id: int, body: Close):
    _errors(M.close, case_id, body.decision, body.analyst.strip(), body.note.strip())
    return {"ok": True}


@app.post("/api/accounts/{account_id}/unfreeze")
def unfreeze(account_id: str, body: Analyst):
    _errors(M.unfreeze, account_id, body.analyst.strip())
    return {"ok": True}


# ------------------------------------------------------------------ training labels
@app.get("/api/labels")
def labels():
    with db.Session() as s:
        return [{"txn_id": l.txn_id, "case_id": l.case_id, "is_fraud": l.is_fraud, "decided_by": l.decided_by,
                 "decided_at": iso(l.decided_at), "model": l.model, "model_probability": l.model_probability,
                 "agent_disposition": l.agent_disposition, "source": l.source}
                for l in s.scalars(select(Label).order_by(Label.id.desc()))]


@app.get("/api/labels.csv")
def labels_csv():
    rows = labels()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["txn_id", "is_fraud"])
    w.writeheader()
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=analyst_labels.csv"})


# ------------------------------------------------------------------ replay feed
class ReplayIn(BaseModel):
    rate: float = 2.0


@app.get("/api/replay")
def replay_status():
    return M.replay.status()


@app.post("/api/replay/start")
def replay_start(body: ReplayIn):
    M.replay.start(body.rate)
    return M.replay.status()


@app.post("/api/replay/stop")
def replay_stop():
    M.replay.stop()
    return M.replay.status()


_examples = None


@app.get("/api/replay/examples")
def examples():
    global _examples
    _examples = _examples or M.history.examples()
    return {k: len(v) for k, v in _examples.items()}


class Inject(BaseModel):
    kind: str


@app.post("/api/replay/inject")
def inject(body: Inject):
    """Send one test-period example of a kind (a fraud type or a legitimate look-alike)."""
    examples()
    ids = _examples.get(body.kind)
    if not ids:
        raise HTTPException(404, f"No examples of {body.kind}.")
    with db.Session() as s:
        seen = set(s.scalars(select(Transaction.txn_id).where(Transaction.txn_id.in_(ids))))
    fresh = [i for i in ids if i not in seen]
    if not fresh:
        raise HTTPException(409, f"Every {body.kind} example has been sent already.")
    out = M.ingest(fresh[0])
    out["injected_kind"] = body.kind  # the demo operator knows what they sent; the analyst view doesn't show it
    out["remaining"] = len(fresh) - 1
    return out


# ------------------------------------------------------------------ the customer's phone
@app.get("/api/phone")
def phone(account_id: str | None = None):
    snap = M.phone.snapshot()
    with db.Session() as s:
        q = select(Notification).order_by(Notification.id.desc()).limit(20)
        if account_id:
            q = q.where(Notification.account_id == account_id)
        snap["notifications"] = [{"id": n.id, "account_id": n.account_id, "at": iso(n.at), "text": n.text}
                                 for n in s.scalars(q)]
    return snap


class Answer(BaseModel):
    accept: bool


@app.post("/api/phone/answer")
def phone_answer(body: Answer):
    if not M.phone.answer(body.accept):
        raise HTTPException(409, "The phone isn't ringing.")
    return {"ok": True}


class Reply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.post("/api/phone/reply")
def phone_reply(body: Reply):
    if not M.phone.reply(body.text):
        raise HTTPException(409, "The agent isn't waiting for an answer.")
    return {"ok": True}


@app.post("/api/phone/hangup")
def phone_hangup():
    M.phone.hang_up_from_phone()
    return {"ok": True}


@app.post("/api/phone/transcribe")
async def phone_transcribe(request: Request):
    """The customer's recorded answer -> text, with faster-whisper on this machine."""
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "No audio received.")
    try:
        return {"text": await run_in_threadpool(voice.transcribe, audio, M.phone.language)}
    except Exception as e:
        raise HTTPException(500, f"Speech recognition failed: {type(e).__name__}: {e}")


@app.get("/api/audio/{name}")
def audio(name: str):
    path = config.TTS_CACHE / name.replace("/", "").replace("\\", "")
    if not path.exists() or path.suffix != ".wav":
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


# ------------------------------------------------------------------ dashboard (built)
@app.get("/{path:path}", include_in_schema=False)
def dashboard(path: str):
    dist = config.DASHBOARD_DIST
    if path.startswith("api/"):
        raise HTTPException(404)
    f = (dist / path).resolve()
    if path and f.is_file() and dist.resolve() in f.parents:
        return FileResponse(f)
    if (dist / "index.html").exists():
        return FileResponse(dist / "index.html")
    return {"message": "API is running. Build the dashboard (cd dashboard && npm install && npm run build) "
                       "or run it in dev mode (npm run dev)."}
