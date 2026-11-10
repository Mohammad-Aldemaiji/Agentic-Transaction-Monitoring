import { useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import { api } from "./api";
import { ErrorBox, useResource } from "./ui";
export const investigationSteps = [
  {
    key: "overview",
    tab: "Overview",
    title: "Overview",
    hint: "Review payment and customer context.",
  },
  {
    key: "scoring",
    tab: "Evidence",
    title: "Scoring evidence",
    hint: "Check the score and available model explanations.",
  },
  {
    key: "assessment",
    tab: "AI assessment",
    title: "AI assessment",
    hint: "Assess AI advice or record why you reviewed manually.",
  },
  {
    key: "verification",
    tab: "Calls",
    title: "Verification",
    hint: "Review the contact outcome or explain why contact is unnecessary.",
  },
  {
    key: "recommendation",
    tab: "Actions",
    title: "Recommendation",
    hint: "Submit your recommendation for independent supervisor review.",
  },
];
export type Progress = Record<
  string,
  {
    outcome: string;
    reason: string;
    reviewer_id: string;
    evidence_version: number;
  }
>;
export default function InvestigationGuide({
  id,
  version,
  revision,
  tab,
  onTab,
  onRefresh,
  editable,
  pending,
}: {
  id: string;
  version: number;
  revision: number;
  tab: string;
  onTab: (tab: string) => void;
  onRefresh: () => void;
  editable: boolean;
  pending: boolean;
}) {
  const progress = useResource<Progress>(
    `/cases/${id}/investigation-progress`,
    revision,
  );
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState("REVIEWED");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const index = investigationSteps.findIndex((s) => s.tab === tab);
  const step = investigationSteps[index];
  const done = Object.keys(progress.data ?? {}).length;
  return (
    <section
      className="investigation-guide"
      aria-label="Investigation progress"
    >
      <div className="guide-heading">
        <div>
          <span className="eyebrow">GUIDED INVESTIGATION</span>
          <h2>
            {pending ? "Awaiting supervisor review" : "Evidence before action"}
          </h2>
        </div>
        <span className="guide-count">{done} of 4 reviews recorded</span>
      </div>
      <ol className="guide-steps">
        {investigationSteps.map((s, i) => (
          <li key={s.key}>
            <button
              type="button"
              aria-label={`Step ${i + 1}: ${s.title}`}
              aria-current={tab === s.tab ? "step" : undefined}
              className={tab === s.tab ? "selected" : ""}
              onClick={() => {
                onTab(s.tab);
                setReason("");
                setOutcome("REVIEWED");
              }}
            >
              <span
                className={
                  progress.data?.[s.key]
                    ? "step-number complete"
                    : "step-number"
                }
              >
                {progress.data?.[s.key] ? <Check size={16} /> : i + 1}
              </span>
              <span>{s.title}</span>
            </button>
          </li>
        ))}
      </ol>
      <ErrorBox error={progress.error ?? error} />
      {step && (
        <div className="guide-current">
          <div>
            <strong>
              Step {index + 1}: {step.title}
            </strong>
            <p>{step.hint}</p>
            {progress.data?.[step.key] && (
              <small>Recorded: {progress.data[step.key].reason}</small>
            )}
          </div>
          {editable && !pending && index < 4 && (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setError(null);
                try {
                  await api(`/cases/${id}/investigation-progress`, {
                    method: "POST",
                    body: {
                      expected_version: version,
                      step: step.key,
                      outcome,
                      reason,
                    },
                  });
                  onRefresh();
                  onTab(investigationSteps[index + 1].tab);
                  setReason("");
                  setOutcome("REVIEWED");
                } catch (e) {
                  setError(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {(index === 2 || index === 3) && (
                <label>
                  Review approach
                  <select
                    value={outcome}
                    onChange={(e) => setOutcome(e.target.value)}
                  >
                    <option value="REVIEWED">Evidence reviewed</option>
                    <option
                      value={index === 2 ? "MANUAL_REVIEW" : "NOT_REQUIRED"}
                    >
                      {index === 2
                        ? "Continue with manual assessment"
                        : "Customer contact not required"}
                    </option>
                  </select>
                </label>
              )}
              <label>
                Review note
                <textarea
                  aria-label="Step review note"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  required
                  minLength={5}
                  maxLength={2000}
                  placeholder={
                    index === 3
                      ? "Summarize the verification outcome or explain why contact is unnecessary…"
                      : "Record your review and any relevant observations…"
                  }
                />
              </label>
              <button
                disabled={busy || !progress.data || !!progress.error}
                type="submit"
              >
                {busy ? "Saving…" : "Save review and continue"}
                <ChevronRight size={16} />
              </button>
            </form>
          )}
        </div>
      )}
    </section>
  );
}
