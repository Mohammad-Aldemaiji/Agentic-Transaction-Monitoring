"""The investigation agent, in the two steps of the sequence diagram.

1. analyse()  - the alert arrives with the model's score and top reasons. The agent
                looks up the transaction, the account's history and the device, and
                recommends one action. It cannot act.
2. verify()   - runs only after an analyst approves VERIFY_WITH_CUSTOMER. The agent
                calls the customer and returns an outcome. Code, not the model, has the
                last word on the outcome (see _safe_outcome).

All per-case state lives in the objects created here, so several cases can be
handled at once without interfering.
"""
import json
from typing import Callable, Protocol

import pandas as pd

from src.agent import llm
from src.agent.llm import Tool, schema

ACTIONS = ["RELEASE", "VERIFY_WITH_CUSTOMER", "BLOCK_AND_FREEZE"]
OUTCOMES = ["RELEASE", "BLOCK_AND_FREEZE", "ESCALATE"]

# What the agent may see. Labels never; residency and corridor fields are left out on
# purpose (fairness note in the README).
VISIBLE = [
    "txn_id", "timestamp", "account_id", "direction", "amount_sar", "channel",
    "entry_mode", "merchant_category", "merchant_name", "merchant_country",
    "counterparty_id", "counterparty_type", "counterparty_country", "status",
    "decline_reason", "device_id", "ip_country", "is_proxy_vpn", "auth_method",
    "is_3ds_authenticated", "seconds_since_login", "txn_city",
    "distance_from_home_km", "customer_type", "home_city", "account_age_days",
    "declared_monthly_income", "amount_over_p95_est", "is_new_counterparty",
    "beneficiary_age_days", "is_new_device", "is_new_country", "txn_count_1h",
    "declined_count_24h", "accounts_sharing_device", "impossible_travel_kmh",
    "cnp_ratio_24h", "small_then_large_flag", "balance_before",
]


class Phone(Protocol):
    def ask(self, text: str) -> str | None: ...   # None: the customer hung up or never answered
    def hang_up(self) -> None: ...


# ------------------------------------------------------------------ look-up tools

class CaseTools:
    """The look-up tools for one alert. Nothing after the alert time is visible."""

    def __init__(self, history, txn_id: str):
        self.h = history
        self.txn_id = txn_id
        self.alert_time = history.row(txn_id)["ts"]

    def _cap(self, value: str) -> pd.Timestamp:
        t = pd.Timestamp(value) if value else self.alert_time
        t = t.tz_localize("Asia/Riyadh") if t.tzinfo is None else t
        return min(t, self.alert_time)

    def get_transaction(self, txn_id: str) -> str:
        if not self.h.has(txn_id) or self.h.row(txn_id)["ts"] > self.alert_time:
            return f"Transaction {txn_id} not found."
        r = self.h.row(txn_id)
        return json.dumps({k: _plain(r[k]) for k in VISIBLE}, default=str, ensure_ascii=False)

    def get_account_history(self, account_id: str, before_timestamp: str = "") -> str:
        h = self.h.account_rows(account_id, self._cap(before_timestamp))
        if h.empty:
            return "No prior history in the last 90 days."
        return json.dumps({
            "n_txns_90d": len(h),
            "median_amount_sar": round(float(h.amount_sar.median()), 2),
            "p95_amount_sar": round(float(h.amount_sar.quantile(0.95)), 2),
            "top_channels": h.channel.value_counts().head(4).to_dict(),
            "top_cities": h.txn_city.value_counts().head(4).to_dict(),
            "merchant_countries_seen": sorted(h.merchant_country.dropna().unique().tolist())[:10],
            "ip_countries_seen": sorted(h.ip_country.dropna().unique().tolist())[:10],
            "distinct_devices": int(h.device_id.nunique()),
            "usual_hours": h.ts.dt.hour.value_counts().head(4).index.tolist(),
            "last_10": _records(h.tail(10)[["timestamp", "direction", "amount_sar", "channel",
                                            "merchant_name", "txn_city", "status"]]),
        }, default=str, ensure_ascii=False)

    def get_device_links(self, device_id: str, before_timestamp: str = "") -> str:
        d = self.h.device_rows(device_id, self._cap(before_timestamp))
        if d.empty:
            return "Device not seen before."
        first = d.groupby("account_id").ts.min().sort_values()
        return json.dumps({"n_accounts": len(first),
                           "accounts_first_seen": first.astype(str).to_dict()})

    def tools(self) -> list:
        return [
            Tool("get_transaction",
                 "Full details of a transaction: amount, channel, merchant, device, session, "
                 "location and behavioural features.",
                 schema(["txn_id"], txn_id={"type": "string"}), self.get_transaction),
            Tool("get_account_history",
                 "The account's normal behaviour over the 90 days before a time: typical amounts, "
                 "channels, cities, countries, hours, devices, and the last 10 transactions.",
                 schema(["account_id"], account_id={"type": "string"},
                        before_timestamp={"type": "string", "description": "ISO time; defaults to the alert time"}),
                 self.get_account_history),
            Tool("get_device_links",
                 "Accounts that used this device up to a time, with when each first appeared. "
                 "Many accounts on one device can mean takeover or mules, but households share devices too.",
                 schema(["device_id"], device_id={"type": "string"},
                        before_timestamp={"type": "string", "description": "ISO time; defaults to the alert time"}),
                 self.get_device_links),
        ]


def _records(df) -> list:
    return [{k: _plain(v) for k, v in row.items()} for row in df.to_dict("records")]


def _plain(v):
    if isinstance(v, float) and v != v:
        return None
    return v.item() if hasattr(v, "item") else v


# ------------------------------------------------------------------ step 1: analyse

ANALYSE_PROMPT = """You are a fraud investigation agent at a Saudi retail bank. The fraud model
has held a transaction and raised an alert. Investigate it the way an experienced
fraud analyst would, then recommend ONE action for a human analyst to approve.
You cannot act yourself.

Actions:
- RELEASE: the evidence shows normal behaviour for this customer; let the transaction through.
- VERIFY_WITH_CUSTOMER: the bank calls the customer on their registered number to confirm.
  Use this whenever the customer's answer would decide the case.
- BLOCK_AND_FREEZE: block the transaction and freeze the account. Only when the evidence of
  fraud is strong and independent of anything the customer could confirm (e.g. card
  testing, impossible travel, a device shared with many unrelated accounts).

Rules:
- Use the lookup tools before deciding. Only state facts a tool returned or that are in
  the alert below. Never invent numbers or history.
- Compare the transaction with the account's own normal behaviour.
- Treat innocent explanations (travel, a planned large purchase, a shared household device)
  as seriously as fraudulent ones. The model's reasons are evidence, not a verdict.
- Confidence must be realistic: above 0.9 only when several independent facts agree.

When you are done, reply with ONLY this JSON:
{"disposition": "LIKELY_FRAUD" | "LIKELY_LEGITIMATE" | "UNCERTAIN",
 "confidence": 0.0-1.0,
 "suspected_pattern": "short name, e.g. account takeover, card testing, none",
 "evidence_for_fraud": ["..."],
 "evidence_against_fraud": ["..."],
 "recommended_action": "RELEASE" | "VERIFY_WITH_CUSTOMER" | "BLOCK_AND_FREEZE",
 "recommendation_reason": "one or two sentences the analyst reads before approving",
 "summary": "two or three sentences for the case file"}"""


def analyse(history, txn_id: str, model_result: dict,
            on_tool: Callable[[str, dict, str], None] = None) -> dict:
    tools = CaseTools(history, txn_id)
    r = history.row(txn_id)
    reasons = "\n".join(f"- {x['label']} = {x['value']} ({x['direction']}, contribution {x['contribution']})"
                        for x in model_result.get("reasons", []))
    user = (f"Alert on transaction {txn_id}, account {r['account_id']}, at {r['timestamp']}.\n"
            f"Model: fraud probability {model_result['probability']:.3f}, band {model_result['band']}.\n"
            f"Top reasons from the model:\n{reasons}\n\nInvestigate it.")
    parsed, raw, usage = llm.run(ANALYSE_PROMPT, user, tools.tools(), on_tool=on_tool)
    if not parsed:
        parsed = {"disposition": "UNCERTAIN", "confidence": 0.0, "suspected_pattern": "unknown",
                  "evidence_for_fraud": [], "evidence_against_fraud": [],
                  "recommended_action": "VERIFY_WITH_CUSTOMER",
                  "recommendation_reason": "The agent did not return a valid analysis; verify with the customer.",
                  "summary": raw[:500]}
    if parsed.get("recommended_action") not in ACTIONS:
        parsed["recommended_action"] = "VERIFY_WITH_CUSTOMER"
    parsed["_usage"] = {"tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out,
                        "steps": usage.steps, "tools": usage.tool_calls}
    return parsed


# ------------------------------------------------------------------ step 2: verify

VERIFY_PROMPT = """You are the bank's fraud verification agent. An analyst approved calling the
customer about a held transaction. Speak to the customer with call_customer, then end
the call with end_call, then report.

The call opens with a fixed security notice, so do not greet or introduce yourself.
On each turn say one or two short spoken sentences.
- Ask whether they made the transaction, naming the amount, date and merchant or
  beneficiary name from the case below.
- If they confirm a transfer to a person or a new beneficiary, ask whether anyone
  contacted them, pressured them, or told them to move money. Scam victims confirm
  payments they were tricked into making.
- Never reveal what the bank detected (IP, country, VPN, device, risk signals), never read
  out account numbers, never ask for a PIN, OTP, password or card number.
- Never tell the customer what the bank will do; that is decided after the call.
- Speak in {language}. Write the final JSON in English.

Case:
{case}

After end_call, reply with ONLY this JSON:
{{"customer_confirms": "YES" | "NO" | "UNCLEAR" | "NO_ANSWER",
 "signs_of_manipulation": true | false,
 "outcome": "RELEASE" | "BLOCK_AND_FREEZE" | "ESCALATE",
 "summary": "two or three sentences for the analyst"}}

RELEASE only if the customer clearly confirms and there is no sign of manipulation.
BLOCK_AND_FREEZE if the customer says they did not make it.
ESCALATE in every other case."""


class _Call:
    def __init__(self, phone: Phone):
        self.phone, self.state, self.transcript = phone, "none", []

    def call_customer(self, question: str) -> str:
        if self.state == "ended":
            return "The call has already ended and cannot be restarted. Continue with what you know."
        self.state = "active"
        self.transcript.append({"who": "agent", "text": question})
        reply = self.phone.ask(question)
        if reply is None:
            self.state = "ended"
            self.transcript.append({"who": "system", "text": "The customer hung up or did not answer."})
            return "The customer hung up or did not answer. The call is over."
        self.transcript.append({"who": "customer", "text": reply})
        return "Customer said: " + reply

    def end_call(self) -> str:
        if self.state != "active":
            return "There is no call in progress."
        self.phone.hang_up()
        self.state = "ended"
        return "Call ended. The customer was told the bank will follow up."

    def tools(self) -> list:
        return [Tool("call_customer", "Say something to the customer on the call and get their reply.",
                     schema(["question"], question={"type": "string"}), self.call_customer),
                Tool("end_call", "Hang up once you have asked everything. Required before the final JSON.",
                     schema(), self.end_call)]


def verify(case: dict, phone: Phone, language: str = "English",
           on_tool: Callable[[str, dict, str], None] = None) -> dict:
    call = _Call(phone)
    system = VERIFY_PROMPT.format(language=language, case=json.dumps(case, ensure_ascii=False, default=str))
    try:
        parsed, raw, usage = llm.run(system, "Start the call.", call.tools(),
                                     must_call="end_call", on_tool=on_tool)
    finally:
        if call.state == "active":  # never leave the customer on the line
            call.end_call()
    parsed = parsed or {"customer_confirms": "UNCLEAR", "signs_of_manipulation": False,
                        "outcome": "ESCALATE", "summary": "The agent did not return a valid result."}
    if call.state == "none" or (call.transcript and call.transcript[-1]["who"] == "system"
                                and not any(t["who"] == "customer" for t in call.transcript)):
        parsed["customer_confirms"] = "NO_ANSWER"
    parsed["outcome"], parsed["outcome_note"] = _safe_outcome(parsed)
    parsed["transcript"] = call.transcript
    return parsed


def _safe_outcome(r: dict):
    """The model proposes, these rules decide. A release needs a clear yes and no sign
    of manipulation; a clear no always blocks; anything else goes back to the analyst."""
    said, manip, proposed = r.get("customer_confirms"), bool(r.get("signs_of_manipulation")), r.get("outcome")
    if said == "NO":
        return "BLOCK_AND_FREEZE", ""
    if said == "YES" and not manip and proposed == "RELEASE":
        return "RELEASE", ""
    if proposed == "RELEASE":
        return "ESCALATE", "Agent proposed release, overridden: the confirmation was not clear or showed signs of manipulation."
    if proposed == "BLOCK_AND_FREEZE" and said != "NO":
        return "ESCALATE", "Agent proposed blocking without a denial from the customer; left for the analyst."
    return "ESCALATE", ""
