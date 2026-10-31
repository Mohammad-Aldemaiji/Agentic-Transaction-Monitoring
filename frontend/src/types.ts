export type Role =
  "analyst" | "supervisor" | "auditor" | "admin" | "participant" | "ingestion";
export interface Identity {
  id: string;
  team: string;
  role: Role;
  sample_traffic_enabled?: boolean;
  booth_mode?: boolean;
}
export interface Page<T> {
  items: T[];
  next_cursor?: string | number | null;
  has_more?: boolean;
  through?: number;
}
export interface Case {
  id: string;
  transaction_id: string;
  state: string;
  version: number;
  assigned_to: string | null;
  created_at: string;
  account_id: string;
  external_id: string;
  amount_minor: number;
  currency: string;
  channel: string;
  risk_score: number | null;
  severity: string;
  review_reason: string;
  processing_status: string;
  simulation: boolean | null;
}
export interface Transaction {
  id: string;
  external_id: string;
  account_id: string;
  amount_minor: number;
  currency: string;
  occurred_at: string;
  channel: string;
  status: string;
  result: Record<string, unknown> | null;
  context: Record<string, unknown>;
  features: Record<string, unknown> | null;
}
export interface Call {
  local_transcription_enabled?: boolean;
  id: string;
  state: string;
  language: "en" | "ar";
  version: number;
  expires_at: string;
}
export interface Proposal {
  id: string;
  case_id: string;
  action: string;
  target: Record<string, unknown>;
  reason: string;
  state: string;
  version: number;
  evidence_version: number;
  proposer_id: string;
  approver_id: string | null;
  expires_at: string;
}
export interface Summary {
  local_agent_enabled?: boolean;
  counts: { notes: number; findings: number };
  case: Case;
  transaction: Transaction;
  investigation: { id: string; state: string; version: number } | null;
  decision: { disposition: string; reason: string; created_at: string } | null;
  active_call: Call | null;
  pending_proposals: number;
  unfinished_approved_actions: number;
}
export interface Dashboard {
  cases: Record<string, number>;
  open_severity: Record<string, number>;
  decisions: Record<string, number>;
  oldest_open_at: string | null;
  review_capacity: {
    target: number | null;
    completed_today: number;
    opened_today: number;
    over_target: boolean;
  } | null;
}
export interface Note {
  id: string;
  text: string;
  author_id: string;
  created_at: string;
}
export interface Event {
  id: number;
  kind: string;
  details: Record<string, unknown>;
  created_at: string;
}
export interface Turn {
  id: string;
  sequence: number;
  speaker: string;
  text: string;
}
export interface Member {
  id: string;
  role: string;
}
export interface Policy {
  id: string;
  version: number;
  state: string;
  threshold: number;
  reason: string;
  proposed_by: string;
  daily_review_capacity: number | null;
  medium_threshold: number;
  high_threshold: number;
  critical_threshold: number;
}
export interface Job {
  id: string;
  kind: string;
  state: string;
  attempts: number;
  error_code: string | null;
}
