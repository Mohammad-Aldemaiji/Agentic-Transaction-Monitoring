import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ACTION_LABEL, CaseDetail, api, clock, human, sar, txnTime, usePoll } from "../api";
import { useAnalyst } from "../App";
import { Risk, Status, Working } from "../components";

const TXN_FIELDS: [string, string][] = [
  ["channel", "Channel"], ["entry_mode", "Entry mode"], ["merchant_category", "Merchant category"],
  ["counterparty_type", "Counterparty type"], ["counterparty_country", "Counterparty country"],
  ["txn_city", "City"], ["distance_from_home_km", "Distance from home (km)"], ["ip_country", "IP country"],
  ["is_proxy_vpn", "Proxy or VPN"], ["auth_method", "Authentication"], ["is_3ds_authenticated", "3-D Secure"],
  ["seconds_since_login", "Seconds since login"], ["is_new_device", "New device"],
  ["is_new_counterparty", "New counterparty"], ["accounts_sharing_device", "Accounts on this device"],
  ["txn_count_1h", "Transactions in last hour"], ["declined_count_24h", "Declines in last 24 h"],
  ["account_age_days", "Account age (days)"], ["customer_type", "Customer type"], ["home_city", "Home city"],
];

export default function CaseView() {
  const { id } = useParams();
  const { data: c, error, reload } = usePoll<CaseDetail>(`/api/cases/${id}`, 1500);
  if (error && !c) return <main className="page"><p className="error">{error}</p></main>;
  if (!c) return <main className="page"><Working>Loading case {id}</Working></main>;
  const t = c.transaction ?? {};

  return (
    <main className="page">
      <p className="hint" style={{ marginBottom: 12 }}><Link to="/">Alerts</Link> / case {c.id}</p>
      <div className="case-top">
        <Risk score={c.risk_score} band={c.band} big />
        <div>
          <div className="amount">SAR {sar(c.amount_sar)}</div>
          <div className="meta">
            {c.counterparty || human(c.channel)}, {human(c.channel)}, {txnTime(c.txn_time)}
          </div>
        </div>
        <div className="meta">
          <Status status={c.status} />
          <div className="hint" style={{ marginTop: 4 }}>
            {human(c.band)} band, fraud probability {(c.probability * 100).toFixed(1)}%, scored in {c.latency_ms?.toFixed(0)} ms
          </div>
        </div>
      </div>

      <div className="case-grid">
        <div>
          <section className="section">
            <h2>Why the model held it</h2>
            <Reasons c={c} />
            <p className="hint" style={{ marginTop: 12 }}>
              Each bar is that field's contribution to this score. Right of the line raised the risk, left of it lowered it.
            </p>
          </section>

          <section className="section">
            <h2>Transaction {c.txn_id}</h2>
            <dl className="kv">
              {TXN_FIELDS.filter(([k]) => k in t).map(([k, label]) => (
                <div key={k}><dt>{label}</dt><dd>{fmt(t[k], k)}</dd></div>
              ))}
              <div><dt>Account</dt><dd>{c.account_id}{c.account_frozen && <> <span className="tag bad">Frozen</span></>}</dd></div>
            </dl>
          </section>

          {c.account_history && (
            <section className="section">
              <h2>The account's last 90 days</h2>
              <dl className="kv" style={{ marginBottom: 14 }}>
                <div><dt>Transactions</dt><dd>{c.account_history.n_txns_90d}</dd></div>
                <div><dt>Typical amount</dt><dd>SAR {sar(c.account_history.median_amount_sar)}</dd></div>
                <div><dt>Large for them (95th pct)</dt><dd>SAR {sar(c.account_history.p95_amount_sar)}</dd></div>
                <div><dt>Devices used</dt><dd>{c.account_history.distinct_devices}</dd></div>
                <div><dt>Usual channels</dt><dd>{Object.keys(c.account_history.top_channels).map(human).join(", ")}</dd></div>
                <div><dt>Usual cities</dt><dd>{Object.keys(c.account_history.top_cities).join(", ")}</dd></div>
              </dl>
              <div className="table-wrap">
                <table className="hist">
                  <thead><tr><th>When</th><th>Channel</th><th>Merchant</th><th className="num">SAR</th><th>Status</th></tr></thead>
                  <tbody>
                    {[...c.account_history.last_10].reverse().map((h, i) => (
                      <tr key={i}>
                        <td>{txnTime(h.timestamp)}</td><td>{human(h.channel)}</td>
                        <td>{h.merchant_name ?? "—"}</td>
                        <td className="num">{h.direction === "CR" ? "+" : "−"}{sar(h.amount_sar)}</td>
                        <td>{human(h.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>

        <div className="decision">
          <AgentPanel c={c} reload={reload} />
          <ApprovePanel c={c} reload={reload} />
          <CallPanel c={c} />
          <ClosePanel c={c} reload={reload} />
          <section className="section">
            <h2>Audit trail</h2>
            <ol className="timeline">
              {c.events.map((e) => (
                <li key={e.id}>
                  <time>{clock(e.at)}</time>
                  <div>
                    <span className="actor">{e.actor}</span>{e.text}
                    {e.kind === "tool" && e.data?.result != null && (
                      <details><summary>What the tool returned</summary><pre>{String(e.data.result)}</pre></details>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>
      </div>
    </main>
  );
}

const YES_NO = new Set(["is_proxy_vpn", "is_3ds_authenticated", "is_new_device", "is_new_counterparty"]);

function fmt(v: unknown, key = "") {
  if (YES_NO.has(key) && (v === 0 || v === 1)) return v ? "Yes" : "No";
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2);
  const s = String(v);
  return /^[A-Z0-9_]+$/.test(s) && s.length > 3 ? human(s) : s;
}

function Reasons({ c }: { c: CaseDetail }) {
  const max = Math.max(...c.reasons.map((r) => Math.abs(r.contribution)), 0.001);
  return (
    <div className="reasons">
      {c.reasons.map((r) => {
        const w = (Math.abs(r.contribution) / max) * 50;
        return (
          <div className="reason" key={r.feature}>
            <span className="label">{r.label}<small>{typeof r.value === "string" ? human(r.value) : String(r.value)}</small></span>
            <span className="track" aria-label={`${r.direction}, ${r.contribution}`}>
              <i className={r.contribution > 0 ? "up" : "down"} style={{ width: `${w}%` }} />
            </span>
            <span className="why">{r.contribution > 0 ? "raised risk" : "lowered risk"}</span>
          </div>
        );
      })}
    </div>
  );
}

function AgentPanel({ c, reload }: { c: CaseDetail; reload: () => void }) {
  const analyst = useAnalyst();
  const [err, setErr] = useState<string | null>(null);
  const a = c.analysis;
  const again = async () => {
    setErr(null);
    try { await api(`/api/cases/${c.id}/reanalyse`, { analyst }); reload(); }
    catch (e) { setErr((e as Error).message); }
  };
  return (
    <section className="section">
      <h2>Agent's investigation</h2>
      {(c.status === "NEW" || c.status === "ANALYSING") && <Working>Looking up the transaction, account history and device</Working>}
      {c.agent_error && (
        <>
          <p className="error">{c.agent_error}</p>
          {c.status === "AWAITING_APPROVAL" && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn small" disabled={!analyst} onClick={again}>Ask the agent again</button>
            </div>
          )}
        </>
      )}
      {a && (
        <>
          <div className="verdict">
            <strong>{human(a.disposition)}</strong>
            <span className="muted">{Math.round(a.confidence * 100)}% confidence</span>
            {a.suspected_pattern && a.suspected_pattern !== "none" && <span className="tag">{a.suspected_pattern}</span>}
          </div>
          <p>{a.summary}</p>
          <div className="evidence">
            <div><h3>Points to fraud</h3><ul>{a.evidence_for_fraud.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            <div><h3>Points against</h3><ul>{a.evidence_against_fraud.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
          </div>
          {a._usage && <p className="hint">{a._usage.steps} model steps, {a._usage.tools.length} look-ups, {a._usage.tokens_in.toLocaleString()} tokens read</p>}
        </>
      )}
      {err && <p className="error">{err}</p>}
    </section>
  );
}

function ApprovePanel({ c, reload }: { c: CaseDetail; reload: () => void }) {
  const analyst = useAnalyst();
  const [lang, setLang] = useState("English");
  const [confirmBlock, setConfirmBlock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!["NEW", "ANALYSING", "AWAITING_APPROVAL"].includes(c.status)) return null;

  const rec = c.recommended_action;
  const approve = async (action: string) => {
    setBusy(true); setErr(null);
    try { await api(`/api/cases/${c.id}/approve`, { analyst, action, language: lang }); reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); setConfirmBlock(false); }
  };
  const order = rec ? [rec, ...["VERIFY_WITH_CUSTOMER", "RELEASE", "BLOCK_AND_FREEZE"].filter((x) => x !== rec)]
                    : ["VERIFY_WITH_CUSTOMER", "RELEASE", "BLOCK_AND_FREEZE"];
  const escalated = c.events.some((e) => e.kind === "escalated") && !rec;

  return (
    <section className="section">
      <h2>Your decision</h2>
      {rec && c.analysis ? (
        <div className="recommend">
          <strong>Agent recommends: {ACTION_LABEL[rec]}</strong>
          <p className="muted" style={{ marginTop: 4 }}>{c.analysis.recommendation_reason}</p>
        </div>
      ) : (
        <div className="recommend none">
          <strong>{escalated ? "Handed back to you" : c.status === "AWAITING_APPROVAL" ? "No recommendation" : "The agent hasn't finished"}</strong>
          <p className="muted" style={{ marginTop: 4 }}>
            {escalated ? "The call didn't settle it. Release the payment or block it." :
              c.status === "AWAITING_APPROVAL" ? "Decide from the model's reasons and the account history." :
              "You can wait for its recommendation or decide now."}
          </p>
        </div>
      )}
      <div className="actions">
        {order.map((action, i) => {
          if (escalated && action === "VERIFY_WITH_CUSTOMER") return null;
          const primary = i === 0 && !!rec;
          if (action === "BLOCK_AND_FREEZE" && confirmBlock) {
            return (
              <div className="row" key={action}>
                <button className="btn danger" disabled={busy} onClick={() => approve(action)}>Yes, block and freeze</button>
                <button className="btn" onClick={() => setConfirmBlock(false)}>Cancel</button>
              </div>
            );
          }
          return (
            <div className="row" key={action}>
              <button
                className={`btn ${primary ? (action === "BLOCK_AND_FREEZE" ? "danger" : "primary") : ""}`}
                disabled={!analyst || busy}
                onClick={() => (action === "BLOCK_AND_FREEZE" ? setConfirmBlock(true) : approve(action))}
              >
                {primary ? `Approve: ${ACTION_LABEL[action]}` : ACTION_LABEL[action]}
              </button>
              {action === "VERIFY_WITH_CUSTOMER" && (
                <select aria-label="Call language" value={lang} onChange={(e) => setLang(e.target.value)}>
                  <option>English</option><option>Arabic</option>
                </select>
              )}
            </div>
          );
        })}
        {!analyst && <p className="hint">Enter your name in the top bar. It's recorded on every decision.</p>}
        {err && <p className="error">{err}</p>}
      </div>
    </section>
  );
}

function CallPanel({ c }: { c: CaseDetail }) {
  if (c.approved_action !== "VERIFY_WITH_CUSTOMER") return null;
  const started = c.events.findIndex((e) => e.kind === "call_started");
  const lines = c.verification?.transcript ??
    c.events.slice(Math.max(0, started)).filter((e) => e.kind === "said" || e.kind === "replied")
      .map((e) => ({ who: e.kind === "said" ? "agent" : "customer", text: e.text }));
  const v = c.verification;
  return (
    <section className="section">
      <h2>Call with the customer</h2>
      {c.status === "CALLING" && !v && (
        <Working>
          {lines.length === 0 ? "Ringing the customer's registered number" : "On the call"}
          {" "}(<a href="/phone" target="_blank" rel="noreferrer">open the customer phone</a>)
        </Working>
      )}
      {lines.length > 0 && (
        <div className="transcript" style={{ marginTop: 12 }}>
          {lines.map((l, i) => (
            <div key={i} className={`l ${l.who}`} dir="auto">{l.text}</div>
          ))}
        </div>
      )}
      {v && (
        <div style={{ marginTop: 12 }}>
          <p><strong>Customer confirms: {human(v.customer_confirms)}</strong>{v.signs_of_manipulation && <> <span className="tag bad">Signs of manipulation</span></>}</p>
          <p className="muted" style={{ marginTop: 4 }}>{v.summary}</p>
          {v.outcome_note && <p className="hint" style={{ marginTop: 6 }}>{v.outcome_note}</p>}
        </div>
      )}
    </section>
  );
}

function ClosePanel({ c, reload }: { c: CaseDetail; reload: () => void }) {
  const analyst = useAnalyst();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const post = async (path: string, body: object) => {
    setBusy(true); setErr(null);
    try { await api(path, body); reload(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  const outcome = c.outcome === "RELEASED" ? "The payment was released." :
    c.outcome === "BLOCKED_AND_FROZEN" ? "The payment was blocked and the account frozen." : null;

  if (c.status === "ACTIONED") {
    return (
      <section className="section">
        <h2>Close the case</h2>
        {outcome && <p style={{ marginBottom: 10 }}>{outcome}</p>}
        <p className="muted" style={{ marginBottom: 10 }}>Your final decision becomes the training label for the next model.</p>
        <textarea placeholder="Note for the case file (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <div className="actions"><div className="row">
          <button className="btn danger" disabled={!analyst || busy} onClick={() => post(`/api/cases/${c.id}/close`, { analyst, decision: "FRAUD", note })}>Close as fraud</button>
          <button className="btn" disabled={!analyst || busy} onClick={() => post(`/api/cases/${c.id}/close`, { analyst, decision: "LEGITIMATE", note })}>Close as legitimate</button>
        </div></div>
        {!analyst && <p className="hint">Enter your name in the top bar first.</p>}
        {err && <p className="error">{err}</p>}
      </section>
    );
  }
  if (c.status !== "CLOSED") return null;
  const right = c.answer_key && (c.answer_key.is_fraud === 1) === (c.final_decision === "FRAUD");
  return (
    <section className="section">
      <h2>Closed as {human(c.final_decision)}</h2>
      <p className="muted">By {c.closed_by}, {clock(c.closed_at)}. {outcome}</p>
      {c.close_note && <p style={{ marginTop: 6 }}>{c.close_note}</p>}
      {c.answer_key && (
        <p style={{ marginTop: 10 }}>
          <span className={`tag ${right ? "live" : "bad"}`}>{right ? "Matches the answer key" : "Doesn't match the answer key"}</span>{" "}
          <span className="hint">
            Synthetic data: {c.answer_key.is_fraud ? `fraud (${human(c.answer_key.fraud_type)})` : "legitimate"}
          </span>
        </p>
      )}
      {c.account_frozen && (
        <div className="actions"><div className="row">
          <button className="btn small" disabled={!analyst || busy} onClick={() => post(`/api/accounts/${c.account_id}/unfreeze`, { analyst })}>Unfreeze the account</button>
        </div></div>
      )}
      {err && <p className="error">{err}</p>}
    </section>
  );
}
