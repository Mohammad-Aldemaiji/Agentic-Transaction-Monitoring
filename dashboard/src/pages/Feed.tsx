import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Stats, Txn, api, clock, human, sar, txnTime, usePoll } from "../api";
import { Decision, Risk } from "../components";

const RATES = [1, 2, 5, 20];

export default function Feed({ stats }: { stats: Stats | null }) {
  const { data: txns, reload } = usePoll<Txn[]>("/api/transactions?limit=100", 1500);
  const { data: examples } = usePoll<Record<string, number>>("/api/replay/examples", 60000);
  const [rate, setRate] = useState(2);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean; case?: number | null } | null>(null);
  const seen = useRef<Set<string>>(new Set());
  const first = useRef(true);
  const r = stats?.replay;

  // Rows that arrived since the last poll get a brief highlight.
  const fresh = new Set<string>();
  for (const t of txns ?? []) if (!seen.current.has(t.txn_id) && !first.current) fresh.add(t.txn_id);
  useEffect(() => {
    if (!txns) return;
    txns.forEach((t) => seen.current.add(t.txn_id));
    first.current = false;
  }, [txns]);

  const start = async () => {
    try { await api("/api/replay/start", { rate }); } catch (e) { setMsg({ text: (e as Error).message, bad: true }); }
  };
  const stop = () => api("/api/replay/stop", {});
  const inject = async (kind: string) => {
    try {
      const t = await api<Txn & { remaining: number }>("/api/replay/inject", { kind });
      const legit = kind.startsWith("LEGIT");
      const what = legit ? "a legitimate payment that looks unusual" : `a ${human(kind).toLowerCase()} fraud`;
      if (t.decision === "HELD") setMsg({ text: `Sent ${what}. The model held it at risk ${t.risk_score}.`, case: t.case_id, bad: legit });
      else if (t.decision === "DECLINED") setMsg({ text: `Sent ${what}. Declined because the account is frozen.` });
      else setMsg({ text: legit ? `Sent ${what}. The model approved it, correctly.`
                                : `Sent ${what}. The model approved it at risk ${t.risk_score}: a missed fraud.`, bad: !legit });
      reload();
    } catch (e) { setMsg({ text: (e as Error).message, bad: true }); }
  };

  return (
    <main className="page">
      <div className="page-head">
        <h1>Live feed</h1>
        <p className="muted">Test-period transactions from the dataset, replayed as a payment stream. The model never trained on them.</p>
      </div>

      <div className="feed-controls">
        <label>
          Speed
          <div className="seg" role="group" aria-label="Transactions per second">
            {RATES.map((x) => <button key={x} className={rate === x ? "on" : ""} onClick={() => setRate(x)}>{x} per second</button>)}
          </div>
        </label>
        {r?.running
          ? <button className="btn" onClick={stop}>Pause the feed</button>
          : <button className="btn primary" onClick={start}>{r && r.position > 0 ? "Resume the feed" : "Start the feed"}</button>}
        {r && (
          <label>
            {r.position.toLocaleString()} of {r.total.toLocaleString()} sent, {r.held} held
            <span className="progress"><i style={{ width: `${(100 * r.position) / Math.max(1, r.total)}%` }} /></span>
          </label>
        )}
        {stats && <label>Scoring time<span>{stats.latency_ms.p50} ms median, {stats.latency_ms.p95} ms 95th percentile</span></label>}
      </div>

      {examples && (
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ marginBottom: 8 }}>Send one example (for the demo; the analyst view doesn't show what it is)</h3>
          <div className="inject">
            {Object.entries(examples).map(([k, n]) => (
              <button key={k} className="btn small" onClick={() => inject(k)} title={`${n} in the test period`}>
                {k.startsWith("LEGIT") ? `Legitimate: ${human(k.replace("LEGIT_", "")).toLowerCase()}` : human(k)}
              </button>
            ))}
          </div>
          {msg && (
            <p className={msg.bad ? "error" : "muted"} style={{ marginTop: 8 }} role="status">
              {msg.text} {msg.case && <Link to={`/cases/${msg.case}`}>Open case {msg.case}</Link>}
            </p>
          )}
        </div>
      )}

      {txns && txns.length === 0 ? (
        <div className="empty"><p>Nothing scored yet.</p><p>Start the feed, or send an example above.</p></div>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Scored</th><th>Risk</th><th>Transaction</th><th className="num">Amount (SAR)</th><th>Decision</th><th className="num">Time to score</th></tr>
            </thead>
            <tbody>
              {(txns ?? []).map((t) => (
                <tr key={t.txn_id} className={fresh.has(t.txn_id) && t.decision === "HELD" ? "new" : ""}>
                  <td className="muted">{clock(t.scored_at)}</td>
                  <td><Risk score={t.risk_score} band={t.band} /></td>
                  <td>{t.counterparty || human(t.channel)}<span className="sub">{human(t.channel)}, {txnTime(t.txn_time)}, {t.txn_id}</span></td>
                  <td className="num">{sar(t.amount_sar)}</td>
                  <td><Decision d={t.decision} />{t.case_id && <span className="sub"><Link to={`/cases/${t.case_id}`}>Case {t.case_id}</Link></span>}</td>
                  <td className="num muted">{t.latency_ms.toFixed(0)} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
