import { useEffect, useState } from "react";
import { api } from "./api";
import { ErrorBox, Panel, Refresh, useAction, useResource } from "./ui";
import type { Transaction } from "./types";
type Payment = Transaction & { case_id: string | null };
export default function BoothFeed({
  readOnly = false,
  openCase,
}: {
  readOnly?: boolean;
  openCase?: (id: string) => void;
}) {
  const [revision, setRevision] = useState(0),
    [account, setAccount] = useState(""),
    [priority, setPriority] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState<Payment | null>(null);
  const customers = useResource<{
    items: {
      name: string;
      account_id: string;
      available: number;
      received: number;
    }[];
  }>("/booth/customers", revision);
  const params = new URLSearchParams({ offset: String(offset) });
  if (account) params.set("account_id", account);
  if (priority) params.set("priority", priority);
  const payments = useResource<{
    items: Payment[];
    has_more: boolean;
    stats: Record<string, number>;
  }>("/booth/payments?" + params, revision);
  const source = useResource<Record<string, unknown>>(
    selected ? `/booth/transactions/${selected.id}/source` : null,
  );
  const action = useAction(() => setRevision((v) => v + 1));
  useEffect(() => {
    const timer = setInterval(() => setRevision((v) => v + 1), 5000);
    return () => clearInterval(timer);
  }, []);
  const stats = payments.data?.stats;
  return (
    <Panel
      title={readOnly ? "Payment monitor" : "Bank feed"}
      aside={<Refresh onClick={() => setRevision((v) => v + 1)} />}
    >
      <div className="panel-body">
        <p>
          {customers.data?.items.length ?? "…"} customers with recorded payment
          histories. Follow incoming payments, review their scores and
          investigate alerts.
        </p>
        {stats && (
          <div className="button-row">
            {["received", "cleared", "flagged", "pending", "failed"].map(
              (k) => (
                <span className="badge" key={k}>
                  {k}: {stats[k]}
                </span>
              ),
            )}
          </div>
        )}
        <p className="muted">
          Scores show the supervised model output × 100, not calibrated
          certainty. A flagged payment can have a low priority because the
          review threshold is deliberately sensitive.
        </p>
        {stats && (
          <p>
            Low: {stats.low} · Medium: {stats.medium} · High: {stats.high} ·
            Critical: {stats.critical}
          </p>
        )}
        {action.feedback}
        <ErrorBox error={customers.error} />
        <ErrorBox error={payments.error} />
        {!readOnly && (
          <button
            disabled={action.busy}
            onClick={() =>
              action.run(async () => {
                const result = await api<{ accepted: number }>("/booth/feed", {
                  method: "POST",
                  body: { count: 100 },
                });
                if (!result.accepted)
                  throw new Error(
                    "All available customer payments have already been received.",
                  );
                setOffset(0);
              }, "Bank payments received for scoring.")
            }
          >
            Simulate incoming traffic · 100 payments
          </button>
        )}
        <div className="button-row" style={{ marginTop: 20 }}>
          <label>
            Customer history{" "}
            <select
              aria-label="Customer history"
              value={account}
              onChange={(e) => {
                setAccount(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All customers</option>
              {customers.data?.items.map((c) => (
                <option key={c.account_id} value={c.account_id}>
                  {c.name} · {c.account_id} · {c.received} received /{" "}
                  {c.available} available
                </option>
              ))}
            </select>
          </label>
          <label>
            Priority{" "}
            <select
              aria-label="Priority"
              value={priority}
              onChange={(e) => {
                setPriority(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All priorities</option>
              {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Customer account</th>
                <th>Payment date</th>
                <th>Amount</th>
                <th>Score / 100</th>
                <th>Priority</th>
                <th>Outcome</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {payments.data?.items.map((p) => (
                <tr key={p.id}>
                  <td>{p.account_id}</td>
                  <td>{new Date(p.occurred_at).toLocaleString()}</td>
                  <td>
                    {(p.amount_minor / 100).toFixed(2)} {p.currency}
                  </td>
                  <td>
                    {typeof p.result?.risk_score === "number"
                      ? (p.result.risk_score * 100).toFixed(2)
                      : "—"}
                  </td>
                  <td>{String(p.result?.severity ?? "—")}</td>
                  <td>
                    {p.case_id
                      ? "Flagged for review"
                      : p.status === "SCORED"
                        ? "Cleared"
                        : p.status}
                  </td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() => setSelected(p)}
                    >
                      JSON
                    </button>
                    {p.case_id && openCase && (
                      <button
                        className="text-button"
                        onClick={() => openCase(p.case_id!)}
                      >
                        Open case
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="button-row">
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </button>
          <span>Page {offset / 50 + 1}</span>
          <button
            className="secondary"
            disabled={!payments.data?.has_more}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </button>
        </div>
        {selected && (
          <section>
            <h3>Bank transaction · {selected.external_id}</h3>
            <ErrorBox error={source.error} />
            <pre className="json-payload">
              {JSON.stringify(source.data ?? selected, null, 2)}
            </pre>
          </section>
        )}
        <p className="muted">
          Synthetic sample data. Historical replay demonstrates workflow; it is
          not an independent accuracy evaluation.
        </p>
      </div>
    </Panel>
  );
}
