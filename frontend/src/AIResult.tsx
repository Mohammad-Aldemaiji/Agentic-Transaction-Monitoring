import { useState } from "react";
import {
  BrainCircuit,
  ShieldAlert,
  ShieldCheck,
  Search,
  ChevronDown,
} from "lucide-react";
import { Panel } from "./ui";
import { label, date } from "./api";
import type { Event } from "./types";
export default function AIResult({
  finding,
  currentVersion,
}: {
  finding: Event;
  currentVersion?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const d = finding.details ?? {};
  const a = (d.analysis ?? {}) as Record<string, unknown>;
  const points = (key: string) =>
    Array.isArray(a[key])
      ? (a[key] as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
  const groups = [
    {
      title: "Signals to investigate",
      key: "evidence_for_fraud",
      icon: ShieldAlert,
      tone: "concern",
    },
    {
      title: "Reassuring evidence",
      key: "evidence_against_fraud",
      icon: ShieldCheck,
      tone: "reassuring",
    },
    {
      title: "Information still needed",
      key: "missing_information",
      icon: Search,
      tone: "unknown",
    },
  ];
  return (
    <Panel title="AI assessment">
      <div className="panel-body ai-assessment">
        <div className="assessment-heading">
          <span className="assessment-icon">
            <BrainCircuit size={28} />
          </span>
          <div>
            <span className="eyebrow">LOCAL AI · ADVISORY</span>
            <h3>Evidence at a glance</h3>
            <small>
              {String(d.model ?? "Local AI")} · {date(finding.created_at)}
            </small>
          </div>
        </div>
        {currentVersion !== undefined &&
          d.evidence_version !== currentVersion && (
            <p className="notice warning" role="alert">
              Evidence has changed. Refresh the assessment before relying on its
              recommendation.
            </p>
          )}
        <p className="ai-summary">
          {String(d.summary ?? a.summary ?? "Assessment completed.")}
        </p>
        <div className="assessment-grid">
          {groups.map((g) => (
            <section key={g.key} className={`assessment-section ${g.tone}`}>
              <h4>
                <g.icon size={18} />
                {g.title}
              </h4>
              {points(g.key).length ? (
                <ul>
                  {points(g.key).map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  {g.key === "missing_information"
                    ? "No additional gaps listed. Check that the available evidence is sufficient."
                    : "No supporting points recorded."}
                </p>
              )}
            </section>
          ))}
        </div>
        <div className="assessment-next">
          <span className="eyebrow">SUGGESTED NEXT STEP</span>
          <h4>
            {label(
              String(
                a.recommended_action ?? d.recommendation ?? "Review evidence",
              ),
            )}
          </h4>
          <p>
            {String(
              a.recommendation_reason ??
                "Review the available evidence and record your own assessment.",
            )}
          </p>
        </div>
        <p className="muted">
          AI supports the investigation. The analyst recommends; an independent
          supervisor records the final decision.
        </p>
        <div className="t-acc" data-open={String(expanded)}>
          <button
            className="t-acc-head secondary"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            Supporting technical details{" "}
            <span className="t-acc-chevron">
              <ChevronDown size={16} />
            </span>
          </button>
          <div className="t-acc-panel">
            <div className="t-acc-panel-inner" inert={!expanded}>
              <pre className="json-payload">{JSON.stringify(d, null, 2)}</pre>
            </div>
          </div>
        </div>
      </div>
    </Panel>
  );
}
