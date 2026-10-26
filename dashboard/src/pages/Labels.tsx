import { Link } from "react-router-dom";
import { clock, human, usePoll } from "../api";

interface LabelRow {
  txn_id: string;
  case_id: number;
  is_fraud: number;
  decided_by: string;
  decided_at: string;
  model: string;
  model_probability: number;
  agent_disposition: string | null;
}

export default function Labels() {
  const { data } = usePoll<LabelRow[]>("/api/labels", 5000);
  const agreed = (data ?? []).filter((l) => l.agent_disposition && l.agent_disposition !== "UNCERTAIN");
  const agentRight = agreed.filter((l) => (l.agent_disposition === "LIKELY_FRAUD") === (l.is_fraud === 1)).length;

  return (
    <main className="page">
      <div className="page-head">
        <h1>Analyst decisions</h1>
        <p className="muted">Every closed case, as a label for retraining the model.</p>
        <div className="tools"><a className="btn" href="/api/labels.csv" download>Download as CSV</a></div>
      </div>
      {data && data.length > 0 && (
        <dl className="figures">
          <div><dd>{data.length}</dd><dt>decisions</dt></div>
          <div><dd>{data.filter((l) => l.is_fraud).length}</dd><dt>confirmed fraud</dt></div>
          <div><dd>{data.filter((l) => !l.is_fraud).length}</dd><dt>false alarms</dt></div>
          <div><dd>{agreed.length ? `${agentRight} of ${agreed.length}` : "—"}</dd><dt>agent leaned the same way as the analyst</dt></div>
        </dl>
      )}
      {data && data.length === 0 ? (
        <div className="empty"><p>No decisions yet.</p><p>Close a case and it appears here.</p></div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Case</th><th>Transaction</th><th>Decision</th><th>Agent's view</th><th className="num">Model probability</th><th>By</th><th>When</th></tr></thead>
            <tbody>
              {(data ?? []).map((l) => (
                <tr key={l.case_id}>
                  <td><Link to={`/cases/${l.case_id}`}>{l.case_id}</Link></td>
                  <td>{l.txn_id}</td>
                  <td><span className={`tag ${l.is_fraud ? "bad" : "done"}`}>{l.is_fraud ? "Fraud" : "Legitimate"}</span></td>
                  <td className="muted">{human(l.agent_disposition)}</td>
                  <td className="num">{(l.model_probability * 100).toFixed(1)}%</td>
                  <td>{l.decided_by}</td>
                  <td className="muted">{clock(l.decided_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
