import type { Case, Identity, Summary } from "../src/types";
export const analyst: Identity = {
  id: "11111111-1111-4111-8111-111111111111",
  role: "analyst",
  team: "synthetic-review",
};
export const caseId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const caseRow: Case = {
  id: caseId,
  transaction_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  state: "IN_REVIEW",
  version: 4,
  assigned_to: analyst.id,
  created_at: "2026-10-25T08:30:00Z",
  account_id: "SYN-ACCOUNT-1042",
  external_id: "SYN-TXN-2084",
  amount_minor: 1845000,
  currency: "SAR",
  channel: "TRANSFER",
  risk_score: 87,
  severity: "CRITICAL",
  review_reason: "SCORING_REVIEW",
  processing_status: "SCORED",
  simulation: true,
};
export const summary: Summary = {
  case: caseRow,
  transaction: {
    id: caseRow.transaction_id,
    external_id: caseRow.external_id,
    account_id: caseRow.account_id,
    amount_minor: caseRow.amount_minor,
    currency: "SAR",
    occurred_at: "2026-10-25T08:28:00Z",
    channel: "TRANSFER",
    status: "SCORED",
    result: { risk_score: 0.87, simulation: true },
    context: { destination_account_id: "SYN-DEST-804" },
    features: { cold_start: false },
  },
  investigation: { id: "investigation-1", state: "ACTIVE", version: 1 },
  decision: null,
  active_call: null,
  pending_proposals: 0,
  unfinished_approved_actions: 0,
  counts: { notes: 0, findings: 0 },
};
export const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
export function fixture(path: string) {
  if (path === "/me") return analyst;
  if (path.startsWith("/dashboard"))
    return {
      cases: { OPEN: 8, IN_REVIEW: 12, AWAITING_CUSTOMER: 3, RESOLVED: 27 },
      open_severity: { CRITICAL: 4, HIGH: 6, MEDIUM: 8, LOW: 5 },
      decisions: { LEGITIMATE: 18, CONFIRMED_FRAUD: 6, INCONCLUSIVE: 3 },
      oldest_open_at: caseRow.created_at,
      review_capacity: null,
    };
  if (path === "/cases/" + caseId + "/summary") return summary;
  if (path.startsWith("/cases?"))
    return {
      items: [
        caseRow,
        {
          ...caseRow,
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          account_id: "SYN-ACCOUNT-2051",
          external_id: "SYN-TXN-2085",
          amount_minor: 272000,
          risk_score: 71,
          severity: "HIGH",
          state: "OPEN",
        },
        {
          ...caseRow,
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          account_id: "SYN-ACCOUNT-3093",
          external_id: "SYN-TXN-2086",
          amount_minor: 83900,
          risk_score: null,
          severity: "UNSCORED",
          state: "IN_REVIEW",
          processing_status: "FAILED",
        },
        {
          ...caseRow,
          id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
          account_id: "SYN-ACCOUNT-4085",
          external_id: "SYN-TXN-2087",
          amount_minor: 462000,
          risk_score: 56,
          severity: "MEDIUM",
          state: "AWAITING_CUSTOMER",
        },
      ],
      next_cursor: null,
    };
  return { items: [], next_cursor: null };
}
