import { Panel } from "./ui";
import type { Event } from "./types";
export default function AIResult({
  finding,
  currentVersion,
}: {
  finding: Event;
  currentVersion?: number;
}) {
  const details = finding.details ?? {};
  const analysis = (details.analysis ?? {}) as Record<string, unknown>;
  const points = (key: string) =>
    Array.isArray(analysis[key])
      ? (analysis[key] as unknown[]).filter(
          (v): v is string => typeof v === "string",
        )
      : [];
  return (
    <Panel title="AI investigation result">
      <div className="panel-body ai-result" role="status">
        {currentVersion !== undefined &&
          details.evidence_version !== currentVersion && (
            <p className="notice" role="alert">
              Evidence has changed since this assessment. Review the latest case
              updates before relying on this recommendation.
            </p>
          )}
        <p className="ai-summary">
          {String(details.summary ?? analysis.summary ?? "Analysis completed.")}
        </p>
        <p>
          <strong>Recommended next step:</strong>{" "}
          {String(
            analysis.recommendation_reason ??
              details.recommendation ??
              "Review the evidence",
          )}
        </p>
        <div className="case-columns">
          <div>
            <h4>Reasons for concern</h4>
            <ul>
              {points("evidence_for_fraud").map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h4>Evidence against fraud</h4>
            <ul>
              {points("evidence_against_fraud").map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
        <p className="muted">
          {String(details.model ?? "Local AI")} · Advisory result. The analyst
          records the decision.
        </p>
        <details>
          <summary>Full AI response · JSON</summary>
          <pre className="json-payload">{JSON.stringify(details, null, 2)}</pre>
        </details>
      </div>
    </Panel>
  );
}
