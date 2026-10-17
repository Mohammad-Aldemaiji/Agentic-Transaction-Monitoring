"""The monitoring service: the sequence diagram as code.

    ingest()   transaction in -> model score + reasons -> APPROVED, or HELD with a new case
    analysis worker   agent analyses each new case and recommends an action
    approve()  analyst approves an action (the recommended one or another)
    action worker     executes it: release, block and freeze, or a verification call
    close()    analyst closes the case; the decision is stored as a training label
"""
import queue
import threading
import time
import traceback

import numpy as np
from sqlalchemy import func, select

from src import config
from src.agent import investigator, llm
from src.features.history import History
from src.models.fraud_model import FraudModel
from src.serving import db
from src.serving.db import Account, Case, CaseEvent, Label, Notification, Transaction, now
from src.serving.phone import PhoneLine

ACTION_TEXT = {"RELEASE": "release the payment", "VERIFY_WITH_CUSTOMER": "call the customer",
               "BLOCK_AND_FREEZE": "block the payment and freeze the account"}
OPEN = ["NEW", "ANALYSING", "AWAITING_APPROVAL", "CALLING", "ACTIONED"]
APPROVABLE = ["NEW", "ANALYSING", "AWAITING_APPROVAL"]


class Conflict(Exception):
    """The request doesn't fit the case's current state."""


class Monitor:
    def __init__(self, history: History = None, model: FraudModel = None):
        self.history = history or History()
        text = self.history.df.drop(columns=["ts"]).select_dtypes(exclude="number").columns
        self.model = model or FraudModel(text_fields=text)
        self.phone = PhoneLine()
        self.analysis_q, self.action_q = queue.Queue(), queue.Queue()
        self.latencies = []           # recent scoring latencies in ms
        self.replay = Replay(self)
        db.init()
        self._requeue_unfinished()
        for target in (self._analysis_worker, self._action_worker, self._warm_up):
            threading.Thread(target=target, daemon=True).start()

    def _warm_up(self):
        """Load the language model and Whisper in the background at startup."""
        from src.agent import voice
        try:
            print("Agent:", llm.warm_up())
        except Exception as e:
            print(f"Agent not ready: {e}")
        try:
            t = time.perf_counter()
            with voice._stt_lock:
                voice.load_whisper()
            print(f"Speech recognition: Whisper {config.WHISPER_MODEL} loaded in {time.perf_counter() - t:.1f} s")
        except Exception as e:
            print(f"Speech recognition not ready: {type(e).__name__}: {e}")

    # ================================================================ ingest
    def ingest(self, txn_id: str = None, record: dict = None) -> dict:
        if record is None:
            if not txn_id or not self.history.has(txn_id):
                raise KeyError(f"Transaction {txn_id} not found in the history store.")
            record = self.history.record(txn_id)
        txn_id = record["txn_id"]
        with db.Session() as s:
            old = s.get(Transaction, txn_id)
            if old:
                return {**self._txn_dict(old), "duplicate": True}
        t0 = time.perf_counter()
        account = record["account_id"]
        frozen = self._is_frozen(account)
        result = None if frozen else self.model.score(record)
        latency = (time.perf_counter() - t0) * 1000

        if frozen:
            decision = "DECLINED"
        else:
            decision = "HELD" if result["is_alert"] else "APPROVED"
        cp = record.get("merchant_name") or record.get("counterparty_id") or ""
        with db.write_lock, db.Session() as s:
            t = Transaction(txn_id=txn_id, account_id=account, txn_time=str(record["timestamp"]),
                            amount_sar=float(record["amount_sar"]), channel=record["channel"],
                            counterparty=str(cp)[:120], decision=decision, latency_ms=round(latency, 2),
                            model=self.model.name,
                            probability=result and result["probability"],
                            risk_score=result and result["risk_score"], band=result and result["band"])
            s.add(t)
            if decision == "HELD":
                c = Case(txn_id=txn_id, account_id=account, probability=result["probability"],
                         risk_score=result["risk_score"], band=result["band"], reasons=result["reasons"])
                s.add(c)
                s.flush()
                t.case_id = c.id
                s.add(CaseEvent(case_id=c.id, actor="model", kind="alert",
                                text=f"Held by {self.model.name}: risk {result['risk_score']}, "
                                     f"band {result['band']}, p={result['probability']:.4f}",
                                data={"latency_ms": round(latency, 2)}))
            if decision == "DECLINED":
                s.add(Notification(account_id=account, text=f"A payment of SAR {t.amount_sar:,.2f} was "
                                   "declined because your account is frozen. Please contact the bank."))
            s.commit()
            out = self._txn_dict(t)
        self.latencies = (self.latencies + [latency])[-2000:]
        if decision == "HELD":
            self.analysis_q.put(out["case_id"])
        out["reasons"] = result["reasons"] if result else []
        return out

    def _is_frozen(self, account_id: str) -> bool:
        with db.Session() as s:
            a = s.get(Account, account_id)
            return bool(a and a.frozen)

    # ================================================================ agent: analyse
    def _analysis_worker(self):
        while True:
            case_id = self.analysis_q.get()
            try:
                self._analyse(case_id)
            except Exception:
                traceback.print_exc()

    def _analyse(self, case_id: int):
        with db.write_lock, db.Session() as s:
            c = s.get(Case, case_id)
            if c.status not in ("NEW", "ANALYSING"):
                return
            c.status, c.agent_error = "ANALYSING", None
            s.commit()
            txn_id = c.txn_id
            model_result = {"probability": c.probability, "band": c.band, "reasons": c.reasons}
        self.event(case_id, "agent", "analysis_started", f"Agent ({config.AGENT_MODEL}) started investigating")

        def on_tool(name, args, result):
            self.event(case_id, "agent", "tool", f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})",
                       {"result": result[:4000]})
        try:
            if not self.history.has(txn_id):
                raise llm.LLMUnavailable("This transaction isn't in the history store, so the agent can't look it up.")
            analysis = investigator.analyse(self.history, txn_id, model_result, on_tool=on_tool)
            error = None
        except Exception as e:
            analysis = None
            error = str(e) if isinstance(e, llm.LLMUnavailable) else f"{type(e).__name__}: {e}"
        with db.write_lock, db.Session() as s:
            c = s.get(Case, case_id)
            c.analysis, c.agent_error = analysis, error
            if analysis:
                c.recommended_action = analysis["recommended_action"]
            if c.status == "ANALYSING":
                c.status = "AWAITING_APPROVAL"
            s.commit()
        if analysis:
            u = analysis.get("_usage", {})
            self.event(case_id, "agent", "recommendation",
                       f"Recommends: {ACTION_TEXT[analysis['recommended_action']]}. {analysis.get('recommendation_reason', '')} "
                       f"({u.get('seconds', 0):.0f} s of model time over {u.get('steps', 0)} steps)",
                       {"disposition": analysis.get("disposition"), "confidence": analysis.get("confidence")})
        else:
            self.event(case_id, "system", "agent_error", f"Agent unavailable, decide manually. {error}")

    def reanalyse(self, case_id: int, analyst: str):
        with db.write_lock, db.Session() as s:
            c = self._case(s, case_id)
            if c.status not in ("AWAITING_APPROVAL", "NEW"):
                raise Conflict(f"Case is {c.status}; it can only be re-analysed while awaiting approval.")
            c.status = "NEW"
            s.commit()
        self.event(case_id, analyst, "reanalyse", "Asked the agent to investigate again")
        self.analysis_q.put(case_id)

    # ================================================================ analyst: approve
    def approve(self, case_id: int, action: str, analyst: str, language: str = "English"):
        if action not in investigator.ACTIONS:
            raise ValueError(f"Action must be one of {investigator.ACTIONS}.")
        with db.write_lock, db.Session() as s:
            c = self._case(s, case_id)
            if c.status not in APPROVABLE:
                raise Conflict(f"Case is {c.status}; there is nothing to approve.")
            c.approved_action, c.approved_by, c.approved_at = action, analyst, now()
            c.call_language = language
            c.status = "CALLING" if action == "VERIFY_WITH_CUSTOMER" else "ACTIONED"
            s.commit()
            rec = c.recommended_action
        note = "" if action == rec or not rec else f" (the agent recommended: {ACTION_TEXT[rec]})"
        self.event(case_id, analyst, "approved", f"Approved: {ACTION_TEXT[action]}{note}")
        if action == "VERIFY_WITH_CUSTOMER":
            self.action_q.put(case_id)
        else:
            self._execute(case_id, action, actor=analyst)

    def _execute(self, case_id: int, action: str, actor: str):
        """Carry out RELEASE or BLOCK_AND_FREEZE (mock core-banking calls)."""
        with db.write_lock, db.Session() as s:
            c = s.get(Case, case_id)
            t = s.get(Transaction, c.txn_id)
            if action == "RELEASE":
                t.decision, c.outcome = "RELEASED", "RELEASED"
                s.add(Notification(account_id=c.account_id, text=f"Your payment of SAR {t.amount_sar:,.2f} "
                                   f"to {t.counterparty or t.channel} has been approved."))
                text = "Transaction released"
            else:
                t.decision, c.outcome = "BLOCKED", "BLOCKED_AND_FROZEN"
                a = s.get(Account, c.account_id) or Account(account_id=c.account_id)
                a.frozen, a.changed_at, a.changed_by, a.case_id = True, now(), actor, case_id
                s.merge(a)
                s.add(Notification(account_id=c.account_id, text="For your protection we have blocked a payment "
                                   f"of SAR {t.amount_sar:,.2f} and frozen your account. Please contact the bank."))
                text = "Transaction blocked and account frozen"
            c.status = "ACTIONED"
            s.commit()
        self.event(case_id, actor, "executed", text + " (mock core-banking API)")

    # ================================================================ agent: verify by phone
    def _action_worker(self):
        while True:
            case_id = self.action_q.get()
            try:
                self._verify(case_id)
            except Exception as e:
                traceback.print_exc()
                self._back_to_analyst(case_id, f"Verification call failed: {type(e).__name__}: {e}")

    def _verify(self, case_id: int):
        with db.Session() as s:
            c = s.get(Case, case_id)
            if c.status != "CALLING":
                return
            t = s.get(Transaction, c.txn_id)
            lang, account = c.call_language, c.account_id
            case = {"amount_sar": t.amount_sar, "transaction_time": t.txn_time, "channel": t.channel,
                    "merchant_or_beneficiary": t.counterparty,
                    "investigation_summary": (c.analysis or {}).get("summary", "")}
        self.event(case_id, "agent", "call_started", f"Calling the customer ({lang})")
        call = self.phone.dial(case_id, account, lang)

        def on_tool(name, args, result):
            if name == "call_customer":
                self.event(case_id, "agent", "said", args.get("question", ""))
                self.event(case_id, "customer", "replied", result.removeprefix("Customer said: "))
            elif name == "end_call":
                self.event(case_id, "agent", "call_ended", "Ended the call")
        result = investigator.verify(case, call, lang, on_tool=on_tool)
        with db.write_lock, db.Session() as s:
            c = s.get(Case, case_id)
            c.verification = result
            s.commit()
        self.event(case_id, "agent", "verification",
                   f"Customer confirms: {result['customer_confirms'].lower().replace('_', ' ')}. "
                   f"Signs of manipulation: {'yes' if result.get('signs_of_manipulation') else 'no'}. "
                   f"Outcome: {ACTION_TEXT.get(result['outcome'], 'hand back to the analyst')}. "
                   f"({result.get('model_seconds') or 0:.0f} s of model time during the call)",
                   {"note": result.get("outcome_note")})
        if result["outcome"] == "ESCALATE":
            self._back_to_analyst(case_id, "Verification inconclusive; the analyst decides. "
                                  + (result.get("outcome_note") or ""))
        else:
            self._execute(case_id, result["outcome"], actor="agent")

    def _back_to_analyst(self, case_id: int, text: str):
        with db.write_lock, db.Session() as s:
            c = s.get(Case, case_id)
            c.status = "AWAITING_APPROVAL"
            c.recommended_action = None
            s.commit()
        self.event(case_id, "system", "escalated", text)

    # ================================================================ analyst: close
    def close(self, case_id: int, decision: str, analyst: str, note: str = ""):
        if decision not in ("FRAUD", "LEGITIMATE"):
            raise ValueError("Decision must be FRAUD or LEGITIMATE.")
        with db.write_lock, db.Session() as s:
            c = self._case(s, case_id)
            if c.status != "ACTIONED":
                raise Conflict(f"Case is {c.status}; close it after the approved action has been carried out.")
            c.status, c.final_decision, c.closed_by, c.closed_at, c.close_note = \
                "CLOSED", decision, analyst, now(), note
            s.add(Label(txn_id=c.txn_id, case_id=c.id, is_fraud=int(decision == "FRAUD"), decided_by=analyst,
                        model=self.model.name, model_probability=c.probability,
                        agent_disposition=(c.analysis or {}).get("disposition")))
            s.commit()
        self.event(case_id, analyst, "closed", f"Closed as {decision}" + (f": {note}" if note else ""))

    def unfreeze(self, account_id: str, analyst: str):
        with db.write_lock, db.Session() as s:
            a = s.get(Account, account_id)
            if not a or not a.frozen:
                raise Conflict("Account is not frozen.")
            a.frozen, a.changed_at, a.changed_by = False, now(), analyst
            case_id = a.case_id
            s.add(Notification(account_id=account_id, text="Your account has been unfrozen. Thank you for your patience."))
            s.commit()
        if case_id:
            self.event(case_id, analyst, "unfrozen", f"Unfroze account {account_id}")

    # ================================================================ helpers
    def event(self, case_id, actor, kind, text, data=None):
        with db.write_lock, db.Session() as s:
            s.add(CaseEvent(case_id=case_id, actor=actor, kind=kind, text=text, data=data))
            s.commit()

    @staticmethod
    def _case(s, case_id) -> Case:
        c = s.get(Case, case_id)
        if not c:
            raise KeyError(f"Case {case_id} not found.")
        return c

    def _requeue_unfinished(self):
        """After a restart, pick up cases that were mid-analysis; a call in progress is lost,
        so those go back to the analyst."""
        with db.write_lock, db.Session() as s:
            for c in s.scalars(select(Case).where(Case.status.in_(["NEW", "ANALYSING"]))):
                c.status = "NEW"
                self.analysis_q.put(c.id)
            for c in s.scalars(select(Case).where(Case.status == "CALLING")):
                c.status = "AWAITING_APPROVAL"
                s.add(CaseEvent(case_id=c.id, actor="system", kind="escalated",
                                text="The service restarted during the verification call; decide again."))
            s.commit()

    @staticmethod
    def _txn_dict(t: Transaction) -> dict:
        return {"txn_id": t.txn_id, "account_id": t.account_id, "txn_time": t.txn_time,
                "amount_sar": t.amount_sar, "channel": t.channel, "counterparty": t.counterparty,
                "decision": t.decision, "probability": t.probability, "risk_score": t.risk_score,
                "band": t.band, "latency_ms": t.latency_ms, "case_id": t.case_id,
                "scored_at": db.iso(t.scored_at)}

    def stats(self) -> dict:
        with db.Session() as s:
            by_decision = dict(s.execute(select(Transaction.decision, func.count()).group_by(Transaction.decision)).all())
            by_status = dict(s.execute(select(Case.status, func.count()).group_by(Case.status)).all())
            labels = s.scalar(select(func.count()).select_from(Label))
            frozen = s.scalar(select(func.count()).select_from(Account).where(Account.frozen))
        lat = np.array(self.latencies) if self.latencies else np.array([0.0])
        return {"transactions": sum(by_decision.values()), "by_decision": by_decision,
                "cases_by_status": by_status, "open_cases": sum(by_status.get(k, 0) for k in OPEN),
                "labels": labels, "frozen_accounts": frozen,
                "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 1),
                               "p95": round(float(np.percentile(lat, 95)), 1), "n": len(self.latencies)},
                "model": {"name": self.model.name, "threshold": self.model.threshold},
                "replay": self.replay.status()}


class Replay:
    """Streams test-period transactions into ingest() at a set rate, like a payment feed."""

    def __init__(self, monitor: Monitor):
        self.m = monitor
        self.ids = None
        self.pos = 0
        self.rate = 2.0
        self.running = False
        self.sent = 0
        self.held = 0
        self._stop = threading.Event()

    def start(self, rate: float):
        self.rate = max(0.1, min(float(rate), 200.0))
        if self.running:
            return
        self._ids()
        self._stop.clear()
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            while not self._stop.is_set() and self.pos < len(self.ids):
                t0 = time.perf_counter()
                tid = self.ids[self.pos]
                self.pos += 1
                r = self.m.ingest(tid)
                if not r.get("duplicate"):
                    self.sent += 1
                    self.held += r["decision"] == "HELD"
                self._stop.wait(max(0.0, 1 / self.rate - (time.perf_counter() - t0)))
        finally:
            self.running = False

    def _ids(self) -> list:
        if self.ids is None:
            self.ids = self.m.history.replay_ids()
        return self.ids

    def status(self) -> dict:
        total = len(self._ids())
        return {"running": self.running, "rate": self.rate, "position": self.pos, "total": total,
                "sent": self.sent, "held": self.held}
