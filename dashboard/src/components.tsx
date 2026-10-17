import { Band, STATUS_LABEL } from "./api";

/** The risk score over a short rule; the tick marks 50, the alert line. */
export function Risk({ score, band, big }: { score: number | null; band: Band | null; big?: boolean }) {
  if (score == null || band == null) return <span className="muted">—</span>;
  return (
    <span className={`risk ${big ? "big" : ""}`} title={`Risk ${score} of 100. 50 is the alert line.`}>
      <span className={`n ${band}`}>{score}</span>
      <span className="rule">
        <i className={`bg-${band}`} style={{ width: `${score}%` }} />
        <b />
      </span>
    </span>
  );
}

export function Status({ status }: { status: string }) {
  const kind =
    status === "AWAITING_APPROVAL" ? "attention" :
    status === "ANALYSING" || status === "CALLING" || status === "NEW" ? "live" :
    status === "CLOSED" ? "done" : "";
  return <span className={`tag ${kind}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Decision({ d }: { d: string }) {
  const kind = d === "HELD" ? "attention" : d === "BLOCKED" || d === "DECLINED" ? "bad" : d === "RELEASED" ? "live" : "done";
  const label: Record<string, string> = {
    APPROVED: "Approved", HELD: "Held", RELEASED: "Released", BLOCKED: "Blocked", DECLINED: "Declined (frozen)",
  };
  return <span className={`tag ${kind}`}>{label[d] ?? d}</span>;
}

export function Working({ children }: { children: React.ReactNode }) {
  return <div className="working"><span className="spinner" aria-hidden />{children}</div>;
}
