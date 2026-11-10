import type { Transaction } from "./types";
import { label } from "./api";
import { Badge } from "./ui";
import { useCountUp } from "./Charts";
const object = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
export function ReadableEvidence({
  value,
}: {
  value: Record<string, unknown>;
}) {
  const rows = Object.entries(value).filter(
    ([, v]) => v !== null && ["string", "number", "boolean"].includes(typeof v),
  );
  return rows.length ? (
    <dl className="evidence-facts">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{label(k)}</dt>
          <dd>{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
        </div>
      ))}
    </dl>
  ) : (
    <p>No readable feature values were recorded.</p>
  );
}
export default function ScoringEvidence({
  transaction,
}: {
  transaction: Transaction;
}) {
  const r = transaction.result ?? {};
  const raw = typeof r.risk_score === "number" ? r.risk_score : null;
  const n = useCountUp(
    raw === null ? 0 : Math.max(0, Math.min(100, raw * 100)),
    600,
  );
  const contributions = Array.isArray(r.contributions)
    ? r.contributions.map(object)
    : [];
  const max = Math.max(
    ...contributions.map((c) => Math.abs(Number(c.contribution) || 0)),
    1e-9,
  );
  const metadata = object(r.metadata);
  return (
    <div className="scoring-readable">
      <div className="score-summary">
        <div
          className="score-orbit"
          style={{ "--score-angle": `${n * 3.6}deg` } as React.CSSProperties}
        >
          <strong>
            {raw === null ? "—" : Math.round(n)}
            <small>/100</small>
          </strong>
        </div>
        <div>
          <span className="eyebrow">SUPERVISED MODEL OUTPUT</span>
          <h3>
            {raw === null
              ? "Scoring unavailable"
              : r.severity
                ? `${label(String(r.severity))} priority`
                : "Recorded model score"}
          </h3>
          {!!r.severity && <Badge value={String(r.severity)} />}
          <p>
            {raw === null
              ? "Continue with manual evidence. This payment has not been classified as safe."
              : "A ranking signal for review. This score is not a calibrated probability of fraud."}
          </p>
        </div>
      </div>
      <h4>Why this payment was flagged</h4>
      <p>
        {r.review === true || r.requires_review === true
          ? "The model or active risk policy requested manual review."
          : r.review === true
            ? "The supervised model flagged this payment."
            : "Review the recorded score and policy alongside the transaction context."}
      </p>
      {contributions.length > 0 ? (
        <>
          <h4>What influenced the score</h4>
          <p className="muted">
            Bars compare recorded feature contributions. Positive values raise
            the model output; negative values lower it.
          </p>
          <div className="contribution-list">
            {contributions.map((c, i) => {
              const v = Number(c.contribution) || 0;
              return (
                <div className="contribution" key={i}>
                  <div>
                    <strong>{String(c.label ?? c.feature ?? "Factor")}</strong>
                    <span>{String(c.value ?? "Not recorded")}</span>
                  </div>
                  <div className="contribution-track">
                    <span
                      style={{
                        width: `${(Math.abs(v) / max) * 100}%`,
                        background: v >= 0 ? "var(--crit)" : "var(--good)",
                      }}
                    />
                  </div>
                  <small>
                    {v >= 0 ? "Raises" : "Lowers"} model output ·{" "}
                    {v > 0 ? "+" : ""}
                    {v.toFixed(3)}
                  </small>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <p className="muted">
          Feature contributions were not recorded for this payment.
        </p>
      )}
      {Array.isArray(r.reasons) && (
        <ul>
          {r.reasons
            .filter((x) => typeof x === "string")
            .map((x, i) => (
              <li key={i}>{String(x)}</li>
            ))}
        </ul>
      )}
      <details>
        <summary>Model and policy details</summary>
        <ReadableEvidence
          value={{
            model: r.model_version ?? r.model ?? r.model_name,
            policy: r.severity_policy,
            ...metadata,
          }}
        />
      </details>
      <details>
        <summary>Technical details</summary>
        <pre className="json-payload">{JSON.stringify(r, null, 2)}</pre>
      </details>
    </div>
  );
}
