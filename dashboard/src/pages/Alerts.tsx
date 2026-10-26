import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ACTION_LABEL, CaseRow, Stats, ago, human, sar, txnTime, usePoll } from "../api";
import { Risk, Status } from "../components";

const VIEWS = [["open", "Open"], ["AWAITING_APPROVAL", "Needs decision"], ["CLOSED", "Closed"], ["all", "All"]] as const;

export default function Alerts({ stats }: { stats: Stats | null }) {
  const [view, setView] = useState<string>("open");
  const [sort, setSort] = useState<"risk" | "time">("risk");
  const { data, error } = usePoll<CaseRow[]>(`/api/cases?status=${view}`, 2000);
  const nav = useNavigate();

  const rows = useMemo(() => {
    const r = [...(data ?? [])];
    return sort === "risk" ? r.sort((a, b) => b.risk_score - a.risk_score || b.id - a.id) : r;
  }, [data, sort]);

  return (
    <main className="page">
      <div className="page-head">
        <h1>Alerts</h1>
        <div className="tools">
          <div className="seg" role="group" aria-label="Which alerts">
            {VIEWS.map(([k, label]) => (
              <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>{label}</button>
            ))}
          </div>
          <div className="seg" role="group" aria-label="Sort">
            <button className={sort === "risk" ? "on" : ""} onClick={() => setSort("risk")}>Highest risk</button>
            <button className={sort === "time" ? "on" : ""} onClick={() => setSort("time")}>Newest</button>
          </div>
        </div>
      </div>

      {stats && (
        <dl className="figures">
          <div><dd>{stats.open_cases}</dd><dt>open cases</dt></div>
          <div><dd>{stats.cases_by_status.AWAITING_APPROVAL ?? 0}</dd><dt>waiting on you</dt></div>
          <div><dd>{stats.transactions.toLocaleString()}</dd><dt>transactions scored</dt></div>
          <div><dd>{stats.by_decision.HELD ?? 0}</dd><dt>held now</dt></div>
          <div><dd>{stats.frozen_accounts}</dd><dt>frozen accounts</dt></div>
          <div><dd>{stats.latency_ms.p95} ms</dd><dt>scoring time, 95th percentile</dt></div>
        </dl>
      )}

      {error && <p className="error">Can't load alerts: {error}</p>}
      {data && rows.length === 0 ? (
        <div className="empty">
          <p>No {view === "CLOSED" ? "closed" : view === "all" ? "" : "open"} alerts.</p>
          <p>Start the <Link to="/feed">live feed</Link> or send an example transaction to see the model hold one.</p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Risk</th>
                <th>Transaction</th>
                <th className="num">Amount (SAR)</th>
                <th>Agent</th>
                <th>Status</th>
                <th>Raised</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="click" onClick={() => nav(`/cases/${c.id}`)}>
                  <td><Risk score={c.risk_score} band={c.band} /></td>
                  <td>
                    <Link to={`/cases/${c.id}`} onClick={(e) => e.stopPropagation()}>{c.counterparty === "CASH" ? "Cash" : c.counterparty || human(c.channel)}</Link>
                    <span className="sub">{human(c.channel)}, {txnTime(c.txn_time)}, case {c.id}</span>
                  </td>
                  <td className="num">{sar(c.amount_sar)}</td>
                  <td>
                    {c.recommended_action ? ACTION_LABEL[c.recommended_action] : c.agent_error ? <span className="muted">Agent unavailable</span> : <span className="muted">—</span>}
                    {c.disposition && <span className="sub">{human(c.disposition)}</span>}
                  </td>
                  <td>
                    <Status status={c.status} />
                    {c.final_decision && <span className="sub">{human(c.final_decision)}</span>}
                  </td>
                  <td className="muted">{ago(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
