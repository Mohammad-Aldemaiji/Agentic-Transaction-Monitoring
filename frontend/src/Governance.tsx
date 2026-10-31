import { useState } from "react";
import { api, date, label, short } from "./api";
import {
  Badge,
  Details,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Panel,
  Refresh,
  Title,
  useAction,
  useResource,
} from "./ui";
import type { Identity, Job, Page, Policy } from "./types";
export async function download(path: string, name: string) {
  const value = await api<unknown>(path);
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function AuditTrail() {
  const [rev, setRev] = useState(0),
    [after, setAfter] = useState(0),
    [through, setThrough] = useState<number | undefined>();
  const data = useResource<
    Page<{
      id: number;
      actor: string;
      kind: string;
      object_id: string;
      request_id: string;
      created_at: string;
    }>
  >(
    "/audit/events?limit=50&after=" +
      after +
      (through !== undefined ? "&through=" + through : ""),
    rev,
  );
  const action = useAction();
  return (
    <>
      <Title
        eyebrow="ASSURANCE / AUDIT"
        title="An accountable record"
        actions={
          <>
            <Refresh
              onClick={() => {
                setAfter(0);
                setThrough(undefined);
                setRev((x) => x + 1);
              }}
            />
            <button
              disabled={action.busy}
              onClick={() =>
                action.run(
                  () =>
                    download(
                      "/audit/export" +
                        (data.data?.through !== undefined
                          ? "?through=" + data.data.through
                          : ""),
                      "rasid-audit.json",
                    ),
                  "Audit export downloaded.",
                )
              }
            >
              Export snapshot
            </button>
          </>
        }
      >
        A protected record of access, evidence, and decisions.
      </Title>
      {action.feedback}
      <Panel title="Audit events">
        <ErrorBox error={data.error} />
        {data.loading ? (
          <Loading />
        ) : data.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Actor</th>
                  <th>Object</th>
                  <th>Recorded</th>
                  <th>Request reference</th>
                </tr>
              </thead>
              <tbody>
                {data.data.items.map((e) => (
                  <tr key={e.id}>
                    <td>
                      {label(e.kind.replaceAll(".", "_"))}
                      <small>#{e.id}</small>
                    </td>
                    <td>{e.actor}</td>
                    <td className="break">{e.object_id}</td>
                    <td>{date(e.created_at)}</td>
                    <td className="break">{e.request_id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No events in this snapshot" />
        )}
        <div className="table-footer">
          <span>
            Export requires authentication within the last five minutes.
          </span>
          {data.data?.has_more && (
            <button
              className="secondary"
              onClick={() => {
                setAfter(Number(data.data!.next_cursor));
                setThrough(data.data!.through);
              }}
            >
              Next page
            </button>
          )}
        </div>
      </Panel>
    </>
  );
}
export function Operations() {
  const [rev, setRev] = useState(0);
  const refresh = () => setRev((x) => x + 1);
  const metrics = useResource<{
      jobs: Record<string, number>;
      oldest_ready_job_age_seconds: number;
    }>("/admin/metrics", rev),
    jobs = useResource<Page<Job>>("/admin/jobs", rev),
    action = useAction(refresh);
  return (
    <>
      <Title
        eyebrow="ADMINISTRATION / PROCESSING"
        title="Processing operations"
        actions={<Refresh onClick={refresh} />}
      >
        Inspect failures and recover eligible jobs without changing case
        evidence.
      </Title>
      <ErrorBox error={metrics.error} />
      <div className="stats-grid">
        {["PENDING", "DONE", "FAILED"].map((s) => (
          <div className="stat" key={s}>
            <span>{label(s)} jobs</span>
            <strong>{metrics.data?.jobs[s] ?? "—"}</strong>
          </div>
        ))}
        <div className="stat">
          <span>Oldest ready job</span>
          <strong>
            {metrics.data
              ? Math.round(metrics.data.oldest_ready_job_age_seconds) + "s"
              : "—"}
          </strong>
        </div>
      </div>
      {action.feedback}
      <Panel title="Failed jobs">
        <ErrorBox error={jobs.error} />
        {jobs.loading ? (
          <Loading />
        ) : jobs.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Type</th>
                  <th>Attempts</th>
                  <th>Failure</th>
                  <th>Recovery</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.items.map((j) => (
                  <tr key={j.id}>
                    <td>{short(j.id)}</td>
                    <td>{label(j.kind)}</td>
                    <td>{j.attempts}</td>
                    <td>{j.error_code ?? "Unavailable"}</td>
                    <td>
                      <button
                        className="secondary"
                        disabled={action.busy}
                        onClick={() =>
                          action.run(
                            () =>
                              api("/admin/jobs/" + j.id + "/retry", {
                                method: "POST",
                                body: {},
                              }),
                            "Job queued for retry.",
                          )
                        }
                      >
                        Retry job
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No failed jobs">
            Scoring failures with an existing review case must be handled
            through manual review.
          </Empty>
        )}
      </Panel>
    </>
  );
}
export default function Governance({ identity }: { identity: Identity }) {
  const [rev, setRev] = useState(0),
    [before, setBefore] = useState(""),
    [threshold, setThreshold] = useState("0.65"),
    [capacity, setCapacity] = useState(""),
    [medium, setMedium] = useState("0.35"),
    [high, setHigh] = useState("0.65"),
    [critical, setCritical] = useState("0.85"),
    [reason, setReason] = useState("");
  const refresh = () => setRev((x) => x + 1);
  const data = useResource<Page<Policy>>(
      "/admin/risk-policy?limit=20" + (before ? "&before=" + before : ""),
      rev,
    ),
    action = useAction(refresh);
  return (
    <>
      <Title
        eyebrow="GOVERNANCE / RISK POLICY"
        title="Consistent, reviewed controls"
        actions={<Refresh onClick={refresh} />}
      >
        Policy changes are proposed separately and require an independent
        supervisor.
      </Title>
      {action.feedback}
      <div className="case-columns">
        <Panel title="Policy history">
          <div className="panel-body">
            <ErrorBox error={data.error} />
            {data.loading ? (
              <Loading />
            ) : data.data?.items.length ? (
              data.data.items.map((p) => (
                <article className="proposal" key={p.id}>
                  <div className="panel-heading">
                    <strong>Policy version {p.version}</strong>
                    <Badge value={p.state} />
                  </div>
                  <p>{p.reason}</p>
                  <dl className="detail-list">
                    <dt>Review threshold</dt>
                    <dd>{p.threshold}</dd>
                    <dt>Daily review target</dt>
                    <dd>{p.daily_review_capacity ?? "Not set"}</dd>
                    <dt>Severity cutoffs</dt>
                    <dd>
                      {p.medium_threshold} / {p.high_threshold} /{" "}
                      {p.critical_threshold}
                    </dd>
                  </dl>
                  {identity.role === "supervisor" && p.state === "DRAFT" && (
                    <button
                      disabled={action.busy}
                      onClick={() =>
                        action.run(
                          () =>
                            api("/admin/risk-policy/" + p.id + "/activate", {
                              method: "POST",
                              body: {},
                            }),
                          "Policy activated.",
                        )
                      }
                    >
                      Approve and activate
                    </button>
                  )}
                </article>
              ))
            ) : (
              <Empty title="No policy proposals" />
            )}
            <div className="button-row">
              {before && (
                <button className="text-button" onClick={() => setBefore("")}>
                  Newest
                </button>
              )}
              {data.data?.next_cursor && (
                <button
                  className="secondary"
                  onClick={() => setBefore(String(data.data!.next_cursor))}
                >
                  Older policies
                </button>
              )}
            </div>
          </div>
        </Panel>
        {identity.role === "admin" && (
          <Panel title="Propose a policy">
            <form
              className="panel-body"
              onSubmit={(e) => {
                e.preventDefault();
                action.run(
                  () =>
                    api("/admin/risk-policy", {
                      method: "POST",
                      body: {
                        threshold: Number(threshold),
                        daily_review_capacity: capacity
                          ? Number(capacity)
                          : null,
                        medium_threshold: Number(medium),
                        high_threshold: Number(high),
                        critical_threshold: Number(critical),
                        reason,
                      },
                    }),
                  "Policy submitted for independent approval.",
                );
              }}
            >
              <fieldset disabled={action.busy}>
                {[
                  ["Review threshold", threshold, setThreshold],
                  ["Medium severity", medium, setMedium],
                  ["High severity", high, setHigh],
                  ["Critical severity", critical, setCritical],
                ].map(([caption, value, setter]) => (
                  <Field key={String(caption)} label={String(caption)}>
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.01"
                      required
                      value={String(value)}
                      onChange={(e) =>
                        (setter as (v: string) => void)(e.target.value)
                      }
                    />
                  </Field>
                ))}
                <Field label="Daily review target (optional)">
                  <input
                    type="number"
                    min="1"
                    max="100000"
                    step="1"
                    value={capacity}
                    onChange={(e) => setCapacity(e.target.value)}
                  />
                </Field>
                <Field label="Reason for change">
                  <textarea
                    minLength={5}
                    maxLength={2000}
                    required
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </Field>
                <button>Submit policy proposal</button>
              </fieldset>
            </form>
          </Panel>
        )}
      </div>
    </>
  );
}
