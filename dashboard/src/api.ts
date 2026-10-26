import { useCallback, useEffect, useRef, useState } from "react";

export type Band = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Reason {
  feature: string;
  label: string;
  value: string | number | null;
  contribution: number;
  direction: string;
}

export interface CaseRow {
  id: number;
  txn_id: string;
  account_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  probability: number;
  risk_score: number;
  band: Band;
  amount_sar: number | null;
  channel: string | null;
  counterparty: string | null;
  txn_time: string | null;
  recommended_action: string | null;
  disposition: string | null;
  agent_error: boolean | string | null;
  approved_action: string | null;
  outcome: string | null;
  final_decision: string | null;
}

export interface Analysis {
  disposition: string;
  confidence: number;
  suspected_pattern: string;
  evidence_for_fraud: string[];
  evidence_against_fraud: string[];
  recommended_action: string;
  recommendation_reason: string;
  summary: string;
  _usage?: { tokens_in: number; tokens_out: number; steps: number; tools: string[]; seconds?: number };
}

export interface Verification {
  customer_confirms: string;
  signs_of_manipulation: boolean;
  outcome: string;
  outcome_note?: string;
  summary: string;
  transcript: { who: string; text: string }[];
}

export interface CaseEvent {
  id: number;
  at: string;
  actor: string;
  kind: string;
  text: string;
  data: Record<string, unknown> | null;
}

export interface CaseDetail extends CaseRow {
  reasons: Reason[];
  analysis: Analysis | null;
  agent_error: string | null;
  approved_by: string | null;
  approved_at: string | null;
  call_language: string;
  verification: Verification | null;
  closed_by: string | null;
  closed_at: string | null;
  close_note: string | null;
  latency_ms: number | null;
  account_frozen: boolean;
  events: CaseEvent[];
  transaction?: Record<string, string | number | null>;
  account_history?: {
    n_txns_90d: number;
    median_amount_sar: number;
    p95_amount_sar: number;
    top_channels: Record<string, number>;
    top_cities: Record<string, number>;
    distinct_devices: number;
    usual_hours: number[];
    last_10: { timestamp: string; direction: string; amount_sar: number; channel: string; merchant_name: string | null; txn_city: string; status: string }[];
  } | null;
  answer_key?: { is_fraud: number; fraud_type: string | null };
}

export interface Txn {
  txn_id: string;
  account_id: string;
  txn_time: string;
  amount_sar: number;
  channel: string;
  counterparty: string;
  decision: string;
  probability: number | null;
  risk_score: number | null;
  band: Band | null;
  latency_ms: number;
  case_id: number | null;
  scored_at: string;
  injected_kind?: string;
}

export interface Stats {
  transactions: number;
  by_decision: Record<string, number>;
  cases_by_status: Record<string, number>;
  open_cases: number;
  labels: number;
  frozen_accounts: number;
  latency_ms: { p50: number; p95: number; n: number };
  model: { name: string; threshold: number };
  replay: { running: boolean; rate: number; position: number; total: number; sent: number; held: number };
}

export interface Health {
  llm: { ok: boolean; model: string; host: string; error?: string };
  voice: Record<string, { voice: string; installed: boolean; error: string | null }>;
  data_file: string;
  transactions_in_history: number;
  database: string;
}

export interface PhoneState {
  call_no: number;
  state: "idle" | "ringing" | "in_call" | "ended";
  case_id: number | null;
  account_id: string | null;
  language: string;
  code: string;
  waiting: boolean;
  lines: { who: string; text: string; audio: string | null }[];
  notifications: { id: number; account_id: string; at: string; text: string }[];
}

export class ApiError extends Error {}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(typeof d === "string" ? d : `Request failed (${res.status})`);
  }
  return data as T;
}

/** Fetch `path` now and every `ms` milliseconds; returns the latest data and a reload function. */
export function usePoll<T>(path: string | null, ms = 2000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      if (alive.current) { setData(d); setError(null); }
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    const id = setInterval(load, ms);
    return () => { alive.current = false; clearInterval(id); };
  }, [load, ms]);
  return { data, error, reload: load };
}

export const sar = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const ago = (iso: string | null) => {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
};

export const clock = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";

/** "2026-08-14T13:05:11+03:00" -> "14 Aug, 13:05" (the transaction's own Riyadh time) */
export const txnTime = (t: string | null) => {
  if (!t) return "—";
  const m = t.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return t;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m[2] - 1];
  return `${+m[3]} ${mon}, ${m[4]}:${m[5]}`;
};

const CHANNELS: Record<string, string> = {
  MADA_POS: "mada card in store", MADA_ATM: "mada ATM", MADA_ECOM: "mada online", VISA_MC_ECOM: "Visa/Mastercard online",
  IPS_SARIE: "sarie instant transfer", RTGS_SARIE: "sarie high-value transfer", SWIFT_OUTWARD: "SWIFT transfer abroad",
  SADAD_BILL: "SADAD bill", WALLET_TOPUP: "Wallet top-up", EXCHANGE_HOUSE_REMIT: "Exchange-house remittance",
  BNPL_SETTLEMENT: "Buy now, pay later", PAYROLL_CREDIT: "Payroll", CASH_DEPOSIT_CDM: "Cash deposit (machine)",
  CASH_DEPOSIT_BRANCH: "Cash deposit (branch)", CASH_WITHDRAWAL: "Cash withdrawal", GOV_PAYMENT: "Government payment",
  ECOM_NO_3DS: "Online, no 3-D Secure", ECOM_3DS: "Online with 3-D Secure", OTP: "One-time password",
  APP_SOCIAL_ENGINEERING: "Authorised-push-payment scam", CNP_FRAUD_BURST: "Card-not-present burst", ATM_SKIMMING: "ATM skimming",
};

export const human = (s: string | null | undefined) =>
  !s ? "—" : CHANNELS[s] ?? s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

export const ACTION_LABEL: Record<string, string> = {
  RELEASE: "Release payment",
  VERIFY_WITH_CUSTOMER: "Call the customer",
  BLOCK_AND_FREEZE: "Block and freeze account",
};

export const STATUS_LABEL: Record<string, string> = {
  NEW: "Queued for agent",
  ANALYSING: "Agent investigating",
  AWAITING_APPROVAL: "Needs your decision",
  CALLING: "Calling customer",
  ACTIONED: "Ready to close",
  CLOSED: "Closed",
};

export const ANALYST_KEY = "atm.analyst";
