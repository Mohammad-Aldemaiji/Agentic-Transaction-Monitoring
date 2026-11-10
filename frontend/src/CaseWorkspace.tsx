import { useEffect, useState } from "react";
import { ArrowLeft, FileText, Phone, ShieldCheck } from "lucide-react";
import { api, date, label, money, short } from "./api";
import {
  Badge,
  Details,
  Empty,
  ErrorBox,
  Field,
  Loading,
  Panel,
  Refresh,
  useAction,
  useResource,
} from "./ui";
import CallPanel from "./CallPanel";
import AIResult from "./AIResult";
import ScoringEvidence, { ReadableEvidence } from "./ScoringEvidence";
import { SEVERITY_COLOR } from "./Charts";
import type {
  Call,
  Event,
  Identity,
  Member,
  Note,
  Page,
  Proposal,
  Summary,
  Transaction,
} from "./types";
function Collection<T>({
  path,
  revision,
  render,
}: {
  path: string;
  revision: number;
  render: (items: T[]) => React.ReactNode;
}) {
  const [cursor, setCursor] = useState<string | number>("");
  const data = useResource<Page<T>>(
    path +
      (path.includes("?") ? "&" : "?") +
      "limit=30" +
      (cursor ? "&after=" + cursor : ""),
    revision,
  );
  return (
    <>
      <ErrorBox error={data.error} />
      {data.loading ? <Loading /> : data.data && render(data.data.items)}
      <div className="button-row">
        {cursor !== "" && (
          <button className="text-button" onClick={() => setCursor("")}>
            First page
          </button>
        )}
        {data.data?.next_cursor &&
          String(data.data.next_cursor) !== String(cursor) &&
          data.data.items.length >= 30 && (
            <button
              className="secondary"
              onClick={() => setCursor(data.data!.next_cursor!)}
            >
              Next page
            </button>
          )}
      </div>
    </>
  );
}
function ProposalCard({
  p,
  identity,
  onChange,
  currentVersion,
  onResubmit,
}: {
  p: Proposal;
  identity: Identity;
  onChange: () => void;
  currentVersion: number;
  onResubmit: () => void;
}) {
  const action = useAction(onChange),
    execution = useResource<{
      status: string;
      receipt?: unknown;
      error_code?: string;
    }>("/action-proposals/" + p.id + "/execution");
  const [reason, setReason] = useState("");
  const canApprove =
    identity.role === "supervisor" && identity.id !== p.proposer_id;
  const canCancel =
    identity.role === "supervisor" ||
    (p.state === "PENDING" && identity.id === p.proposer_id);
  return (
    <article className="proposal">
      <div className="panel-heading">
        <strong>{label(p.action)}</strong>
        <Badge value={p.state} />
      </div>
      <p>{p.reason}</p>
      <Details value={p.target} />
      <small className="muted">
        Evidence version {p.evidence_version}
        {p.state === "PENDING" ? " · Pending until supervisor review" : ""}
        {p.created_at ? " · Submitted " + date(p.created_at) : ""}
      </small>
      <ErrorBox error={execution.error} />
      {execution.data && (
        <p>
          Execution: <Badge value={execution.data.status} />
        </p>
      )}
      {execution.data?.receipt !== undefined && (
        <Details value={execution.data.receipt} />
      )}
      {p.state === "PENDING" && p.evidence_version !== currentVersion && (
        <p className="notice warning">
          Evidence changed after submission. Return or withdraw this
          recommendation and submit a revised review before approval.
        </p>
      )}
      {p.state === "EXPIRED" && identity.role === "analyst" && (
        <button className="secondary" onClick={onResubmit}>
          Review and resubmit
        </button>
      )}
      {action.feedback}
      <fieldset disabled={action.busy}>
        {p.state === "PENDING" && canApprove && (
          <div className="button-row">
            <button
              disabled={p.evidence_version !== currentVersion}
              onClick={() =>
                action.run(
                  () =>
                    api("/action-proposals/" + p.id + "/approval", {
                      method: "POST",
                      body: { expected_version: p.version, approved: true },
                    }),
                  "Approved. Execution is queued.",
                )
              }
            >
              Approve action
            </button>
            <button
              className="secondary"
              onClick={() =>
                action.run(
                  () =>
                    api("/action-proposals/" + p.id + "/approval", {
                      method: "POST",
                      body: { expected_version: p.version, approved: false },
                    }),
                  "Proposal rejected.",
                )
              }
            >
              Reject
            </button>
          </div>
        )}
        {p.state === "PENDING" && !canApprove && (
          <p className="muted">
            Awaiting an independent supervisor. Re-authenticate before approving
            if your MFA is older than five minutes.
          </p>
        )}
        {["PENDING", "APPROVED"].includes(p.state) &&
          canCancel &&
          execution.data?.status !== "SUCCEEDED" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                action.run(
                  () =>
                    api("/action-proposals/" + p.id + "/cancel", {
                      method: "POST",
                      body: { expected_version: p.version, reason },
                    }),
                  "Proposal cancelled.",
                );
              }}
            >
              <Field label="Reason for withdrawal or further investigation">
                <input
                  minLength={5}
                  maxLength={2000}
                  required
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </Field>
              <button className="secondary danger">
                {identity.role === "supervisor"
                  ? "Return for further investigation"
                  : "Withdraw proposal"}
              </button>
            </form>
          )}
      </fieldset>
    </article>
  );
}
export default function CaseWorkspace({
  id,
  identity,
  onBack,
}: {
  id: string;
  identity: Identity;
  onBack: () => void;
}) {
  const [rev, setRev] = useState(0),
    [tab, setTab] = useState("Overview"),
    [callId, setCallId] = useState("");
  const [aiResult, setAiResult] = useState<Event | null>(null);
  const refresh = () => setRev((x) => x + 1);
  const summary = useResource<Summary>("/cases/" + id + "/summary", rev),
    action = useAction(refresh);
  const [note, setNote] = useState(""),
    [reason, setReason] = useState(""),
    [proposal, setProposal] = useState("HOLD_TRANSFER"),
    [disposition, setDisposition] = useState("INCONCLUSIVE"),
    [confirmed, setConfirmed] = useState(false);
  const [participant, setParticipant] = useState(""),
    [language, setLanguage] = useState("en"),
    [assignee, setAssignee] = useState(""),
    [finding, setFinding] = useState(""),
    [category, setCategory] = useState("TRANSACTION_PATTERN"),
    [recommendation, setRecommendation] = useState("REVIEW");
  const write = identity.role === "analyst" || identity.role === "supervisor";
  const participants = useResource<Page<Member>>(
      write ? "/team/members?role=participant&limit=100" : null,
      rev,
    ),
    analysts = useResource<Page<Member>>(
      identity.role === "supervisor"
        ? "/team/members?role=analyst&limit=100"
        : null,
      rev,
    );
  const savedFindings = useResource<Page<Event>>(
    "/cases/" + id + "/findings?latest_ai=true",
    rev,
  );
  const source = useResource<Record<string, unknown>>(
    identity.booth_mode && summary.data?.transaction.id
      ? "/booth/transactions/" + summary.data.transaction.id + "/source"
      : null,
    rev,
  );
  const latestAI = [
    ...(aiResult ? [aiResult] : []),
    ...(savedFindings.data?.items ?? []),
  ]
    .filter((finding) => finding.details?.source === "local_llm")
    .sort((a, b) => Number(b.id) - Number(a.id))[0];
  const data = summary.data;
  useEffect(() => {
    if (!data?.pending_proposals && !data?.unfinished_approved_actions) return;
    const timer = setInterval(() => setRev((x) => x + 1), 5000);
    return () => clearInterval(timer);
  }, [data?.pending_proposals, data?.unfinished_approved_actions]);

  function mutate(path: string, body: unknown, method = "POST") {
    return api(path, { method, body });
  }
  if (!data)
    return (
      <>
        <button className="text-button" onClick={onBack}>
          <ArrowLeft size={16} />
          Back to queue
        </button>
        <ErrorBox error={summary.error} />
        {summary.loading ? <Loading /> : <Refresh onClick={refresh} />}
      </>
    );
  const c = data.case;
  const open = c.state !== "RESOLVED";
  const version = { expected_version: c.version };
  return (
    <>
      <button className="text-button back" onClick={onBack}>
        <ArrowLeft size={16} />
        Back to overview
      </button>
      <section className="case-hero" aria-label={"Case " + short(id)}>
        <span className="hero-ring" aria-hidden="true" />
        <span className="hero-sweep" aria-hidden="true" />
        <span
          className="score-tile"
          style={{
            ["--tile-bar" as string]:
              SEVERITY_COLOR[c.severity] ?? SEVERITY_COLOR.UNSCORED,
          }}
          title={c.risk_score === null ? undefined : c.risk_score.toFixed(2)}
        >
          <strong>
            {c.risk_score === null ? "—" : Math.round(c.risk_score)}
          </strong>
          <small>of 100</small>
        </span>
        <div className="hero-who">
          <div className="eyebrow" style={{ color: "#a5f3fc" }}>
            Case {short(id)}
          </div>
          <h1 className="account" style={{ color: "#ffffff", margin: "2px 0" }}>
            {c.account_id}
          </h1>
          <p>
            {label(c.channel)}
            {typeof data.transaction.context?.destination_account_id ===
            "string"
              ? " to " + data.transaction.context.destination_account_id
              : ""}{" "}
            on {date(data.transaction.occurred_at)}
          </p>
          <div className="hero-tags">
            <span className={"sev-pill " + c.severity.toLowerCase()}>
              {c.risk_score === null ? "Not scored" : label(c.severity)}
            </span>
            <span className="glass-tag">{label(c.state)}</span>
            <span className="glass-tag">
              {c.assigned_to
                ? c.assigned_to === identity.id
                  ? "Assigned to you"
                  : "Assigned to " + short(c.assigned_to)
                : "Nobody assigned yet"}
            </span>
            <span className="glass-tag">Opened {date(c.created_at)}</span>
          </div>
        </div>
        <div className="case-amount">
          <small>AMOUNT</small>
          <strong>{money(c.amount_minor, c.currency)}</strong>
          <div
            className="heading-actions"
            style={{ marginTop: 10, justifyContent: "flex-end" }}
          >
            <Refresh onClick={refresh} />
          </div>
        </div>
      </section>
      {c.processing_status === "FAILED" && (
        <div className="notice warning">
          Scoring was unavailable. Review this case manually; it has not been
          classified as safe.
        </div>
      )}
      {action.feedback}
      <p className="muted" style={{ fontSize: 13 }}>
        The risk score is the model output times 100. It is not a probability,
        and the queue holds alerts only, not every payment.
      </p>

      {identity.booth_mode && (
        <Panel title="Bank transaction">
          <div className="panel-body">
            <ErrorBox error={source.error} />
            <details>
              <summary>Raw JSON as received from the bank</summary>
              <pre className="json-payload">
                {JSON.stringify(source.data ?? data.transaction, null, 2)}
              </pre>
            </details>
          </div>
        </Panel>
      )}
      <nav className="tabs" aria-label="Case sections">
        {[
          "Overview",
          "Evidence",
          "AI assessment",
          "Notes & findings",
          "Calls",
          "Actions",
          "Timeline",
        ].map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            aria-current={tab === t ? "page" : undefined}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <div className="investigation-stage" key={tab}>
        {tab === "AI assessment" && (
          <>
            <Panel title="Assess the evidence">
              <div className="panel-body">
                <p>
                  Use local AI to organize supporting evidence. Review the
                  result alongside the scoring evidence and customer history.
                  Record your conclusions in Notes & findings.
                </p>
                {write && open && data.investigation?.state === "ACTIVE" && (
                  <button
                    disabled={
                      action.busy ||
                      !data.local_agent_enabled ||
                      !!data.active_call
                    }
                    onClick={() =>
                      action.run(async () => {
                        const result = await api<Event>(
                          "/cases/" + id + "/agent-analysis",
                          {
                            method: "POST",
                            body: version,
                            signal: AbortSignal.timeout(155000),
                          },
                        );
                        setAiResult(result);
                      }, "Assessment ready. Review the findings below.")
                    }
                  >
                    {action.busy
                      ? "Assessing evidence…"
                      : "Analyze with local AI"}
                  </button>
                )}
                {!data.local_agent_enabled && (
                  <p className="muted">
                    Local AI is unavailable. You can complete a manual
                    assessment and record your reasoning.
                  </p>
                )}
                {action.busy && (
                  <p className="assessment-loading" role="status">
                    Analyzing transaction context and customer history…
                  </p>
                )}
              </div>
            </Panel>
            {latestAI ? (
              <AIResult finding={latestAI} currentVersion={c.version} />
            ) : (
              <Empty title="Your assessment starts here">
                Run local AI or review the evidence manually. Record the
                approach and rationale in Notes & findings.
              </Empty>
            )}
          </>
        )}
        {tab === "Overview" && (
          <div className="case-columns">
            <div>
              <Panel title="Investigation" aside={<FileText size={19} />}>
                <div className="panel-body">
                  <p>
                    {data.investigation
                      ? "Investigation status: " +
                        label(data.investigation.state)
                      : "Start an investigation to review this transaction and contact the customer."}
                  </p>
                  {write && open && (
                    <fieldset disabled={action.busy}>
                      <div className="button-row">
                        {(!data.investigation ||
                          data.investigation.state === "CANCELLED") && (
                          <button
                            onClick={() =>
                              action.run(() =>
                                mutate("/cases/" + id + "/investigations", {
                                  ...version,
                                  operation: data.investigation
                                    ? "resume"
                                    : "start",
                                }),
                              )
                            }
                          >
                            {data.investigation
                              ? "Resume investigation"
                              : "Begin investigation"}
                          </button>
                        )}
                        {data.investigation?.state === "ACTIVE" && (
                          <button
                            className="secondary danger"
                            onClick={() =>
                              action.run(() =>
                                mutate("/cases/" + id + "/investigations", {
                                  ...version,
                                  operation: "cancel",
                                }),
                              )
                            }
                          >
                            Cancel investigation
                          </button>
                        )}
                      </div>
                    </fieldset>
                  )}
                  <div className="facts">
                    <div>
                      <span>Notes</span>
                      <strong>{data.counts?.notes ?? "—"}</strong>
                    </div>
                    <div>
                      <span>Pending approvals</span>
                      <strong>{data.pending_proposals}</strong>
                    </div>
                    <div>
                      <span>Actions awaiting execution</span>
                      <strong>{data.unfinished_approved_actions}</strong>
                    </div>
                  </div>
                </div>
              </Panel>
              {(identity.role === "supervisor" || data.decision) && (
                <Panel title="Final decision" aside={<ShieldCheck size={19} />}>
                  <div className="panel-body">
                    {data.decision ? (
                      <>
                        <Badge value={data.decision.disposition} />
                        <p>{data.decision.reason}</p>
                        <small>{date(data.decision.created_at)}</small>
                      </>
                    ) : identity.role === "supervisor" && open ? (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          action.run(
                            () =>
                              mutate("/cases/" + id + "/decisions", {
                                ...version,
                                disposition,
                                reason,
                              }),
                            "Final decision recorded.",
                          );
                        }}
                      >
                        <fieldset
                          disabled={
                            action.busy ||
                            data.pending_proposals > 0 ||
                            !!data.active_call ||
                            data.unfinished_approved_actions > 0
                          }
                        >
                          <Field label="Disposition">
                            <select
                              value={disposition}
                              onChange={(e) => setDisposition(e.target.value)}
                            >
                              <option value="INCONCLUSIVE">Inconclusive</option>
                              <option value="LEGITIMATE">Legitimate</option>
                              <option value="CONFIRMED_FRAUD">
                                Confirmed fraud
                              </option>
                            </select>
                          </Field>
                          <Field label="Decision rationale">
                            <textarea
                              required
                              minLength={5}
                              maxLength={2000}
                              value={reason}
                              onChange={(e) => setReason(e.target.value)}
                            />
                          </Field>
                          <label className="check">
                            <input
                              type="checkbox"
                              required
                              checked={confirmed}
                              onChange={(e) => setConfirmed(e.target.checked)}
                            />
                            I have reviewed the evidence. This decision closes
                            the case.
                          </label>
                          <button type="submit">Record final decision</button>
                        </fieldset>
                        {(data.active_call ||
                          data.unfinished_approved_actions > 0) && (
                          <p className="muted">
                            Complete supervisor review, finish active calls and
                            wait for approved actions before closing this case.
                          </p>
                        )}
                      </form>
                    ) : (
                      <p>
                        Awaiting the supervisor’s final decision. Analysts
                        submit recommendations in the Actions tab.
                      </p>
                    )}
                  </div>
                </Panel>
              )}
            </div>
            <div>
              <Panel title="Transaction details">
                <div className="panel-body">
                  <dl className="detail-list">
                    <dt>Transaction</dt>
                    <dd>{c.external_id}</dd>
                    <dt>Amount</dt>
                    <dd>{money(c.amount_minor, c.currency)}</dd>
                    <dt>Occurred</dt>
                    <dd>{date(data.transaction.occurred_at)}</dd>
                    <dt>Processing</dt>
                    <dd>
                      <Badge value={data.transaction.status} />
                    </dd>
                    <dt>Review reason</dt>
                    <dd>{label(c.review_reason)}</dd>
                  </dl>
                  <ReadableEvidence value={data.transaction.context} />
                  {!identity.booth_mode && (
                    <details style={{ margin: "16px 0 0" }}>
                      <summary>Full transaction record (raw JSON)</summary>
                      <pre className="json-payload">
                        {JSON.stringify(data.transaction, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </Panel>
              {identity.role === "supervisor" && open && (
                <Panel title="Assign analyst">
                  <form
                    className="panel-body"
                    onSubmit={(e) => {
                      e.preventDefault();
                      action.run(() =>
                        mutate(
                          "/cases/" + id + "/assignment",
                          { ...version, analyst_id: assignee },
                          "PUT",
                        ),
                      );
                    }}
                  >
                    <ErrorBox error={analysts.error} />
                    <fieldset disabled={action.busy}>
                      <Field label="Analyst">
                        <select
                          required
                          value={assignee}
                          onChange={(e) => setAssignee(e.target.value)}
                        >
                          <option value="">Select analyst</option>
                          {analysts.data?.items.map((a) => (
                            <option key={a.id} value={a.id}>
                              {short(a.id)}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <button>Assign case</button>
                    </fieldset>
                  </form>
                </Panel>
              )}
            </div>
          </div>
        )}
        {tab === "Evidence" && (
          <Evidence id={id} revision={rev} transaction={data.transaction} />
        )}
        {tab === "Notes & findings" && (
          <div className="case-columns">
            <Panel title="Case notes">
              <div className="panel-body">
                {write && open && (
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      if (
                        await action.run(() =>
                          mutate("/cases/" + id + "/notes", {
                            ...version,
                            text: note,
                          }),
                        )
                      )
                        setNote("");
                    }}
                  >
                    <fieldset disabled={action.busy}>
                      <Field label="Add a note">
                        <textarea
                          minLength={5}
                          maxLength={2000}
                          required
                          value={note}
                          onChange={(e) => setNote(e.target.value)}
                        />
                      </Field>
                      <button>Add note</button>
                    </fieldset>
                  </form>
                )}
                <Collection<Note>
                  path={"/cases/" + id + "/notes"}
                  revision={rev}
                  render={(items) =>
                    items.length ? (
                      items.map((n) => (
                        <article className="note" key={n.id}>
                          <p>{n.text}</p>
                          <small>
                            {date(n.created_at)} by {short(n.author_id)}
                          </small>
                        </article>
                      ))
                    ) : (
                      <Empty title="No notes yet" />
                    )
                  }
                />
              </div>
            </Panel>
            <Panel title="Investigation findings">
              <div className="panel-body">
                {write && open && (
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      if (
                        await action.run(() =>
                          mutate("/cases/" + id + "/findings", {
                            ...version,
                            category,
                            summary: finding,
                            recommendation,
                          }),
                        )
                      )
                        setFinding("");
                    }}
                  >
                    <fieldset disabled={action.busy}>
                      <Field label="Category">
                        <select
                          value={category}
                          onChange={(e) => setCategory(e.target.value)}
                        >
                          {[
                            "TRANSACTION_PATTERN",
                            "ACCOUNT_HISTORY",
                            "CUSTOMER_CONTACT",
                            "OTHER",
                          ].map((x) => (
                            <option key={x} value={x}>
                              {label(x)}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Finding">
                        <textarea
                          required
                          minLength={5}
                          maxLength={2000}
                          value={finding}
                          onChange={(e) => setFinding(e.target.value)}
                        />
                      </Field>
                      <Field label="Recommendation">
                        <select
                          value={recommendation}
                          onChange={(e) => setRecommendation(e.target.value)}
                        >
                          {[
                            "REVIEW",
                            "CONTACT_CUSTOMER",
                            "PROPOSE_ACTION",
                            "NO_ACTION",
                          ].map((x) => (
                            <option key={x} value={x}>
                              {label(x)}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <button>Record finding</button>
                    </fieldset>
                  </form>
                )}
                <Collection<Event>
                  path={"/cases/" + id + "/findings"}
                  revision={rev}
                  render={(items) =>
                    items.length ? (
                      items.map((f) => (
                        <article className="note" key={f.id}>
                          <Details value={f.details} />
                          <small>{date(f.created_at)}</small>
                        </article>
                      ))
                    ) : (
                      <Empty title="No findings yet" />
                    )
                  }
                />
              </div>
            </Panel>
          </div>
        )}
        {tab === "Calls" && (
          <div
            className={identity.booth_mode ? "call-workspace" : "case-columns"}
          >
            <Panel title="Customer contact" aside={<Phone size={18} />}>
              <div className="panel-body">
                {write && open && !data.active_call && (
                  <form
                    onSubmit={async (e) => {
                      e.preventDefault();
                      await action.run(
                        async () => {
                          const call = await api<Call>(
                            "/cases/" + id + "/calls",
                            {
                              method: "POST",
                              body: {
                                ...version,
                                participant_id: participant,
                                language,
                              },
                            },
                          );
                          setCallId(call.id);
                        },
                        identity.booth_mode
                          ? "Calling the account holder. Accept the call below."
                          : "Verification call created. The customer can answer in their workspace.",
                      );
                    }}
                  >
                    <ErrorBox error={participants.error} />
                    <fieldset
                      disabled={
                        action.busy || data.investigation?.state !== "ACTIVE"
                      }
                    >
                      <Field
                        label={
                          identity.booth_mode
                            ? "Customer contact"
                            : "Customer participant"
                        }
                      >
                        <select
                          required
                          value={participant}
                          onChange={(e) => setParticipant(e.target.value)}
                        >
                          <option value="">Select contact</option>
                          {participants.data?.items.map((p) => (
                            <option key={p.id} value={p.id}>
                              {identity.booth_mode
                                ? "Account holder"
                                : short(p.id)}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Call language">
                        <select
                          value={language}
                          onChange={(e) => setLanguage(e.target.value)}
                        >
                          <option value="en">English</option>
                          <option value="ar">العربية</option>
                        </select>
                      </Field>
                      <button>Create verification call</button>
                    </fieldset>
                    {data.investigation?.state !== "ACTIVE" && (
                      <p className="muted">
                        Start or resume the investigation first.
                      </p>
                    )}
                  </form>
                )}
                <Collection<Call>
                  path={"/cases/" + id + "/calls"}
                  revision={rev}
                  render={(items) =>
                    items.map((c) => (
                      <button
                        className="inbox-item"
                        key={c.id}
                        onClick={() => setCallId(c.id)}
                      >
                        <span>
                          {short(c.id)} ({c.language.toUpperCase()})
                        </span>
                        <Badge value={c.state} />
                      </button>
                    ))
                  }
                />
              </div>
            </Panel>
            {callId || data.active_call?.id ? (
              <CallPanel
                key={callId || data.active_call!.id}
                callId={callId || data.active_call!.id}
                identity={identity}
                onChange={refresh}
              />
            ) : (
              <Empty title="Select a verification call">
                Calls and replies are simulated and recorded as evidence.
              </Empty>
            )}
          </div>
        )}
        {tab === "Actions" && (
          <div className="case-columns">
            <Panel title="Action proposals">
              <div className="panel-body">
                <section className="recommendation-summary">
                  <h3>Investigation summary</h3>
                  <p>
                    {money(c.amount_minor, c.currency)} · {c.account_id} ·{" "}
                    {label(c.severity)} priority
                  </p>
                  {latestAI && (
                    <p>
                      <strong>AI summary:</strong>{" "}
                      {String(
                        latestAI.details.summary ?? "Assessment recorded",
                      )}
                    </p>
                  )}
                </section>
                <p className="muted">
                  Actions are simulated. An approval does not move money or
                  contact a real bank.
                </p>
                <Collection<Proposal>
                  path={"/cases/" + id + "/action-proposals"}
                  revision={rev}
                  render={(items) =>
                    items.length ? (
                      items.map((p) => (
                        <ProposalCard
                          key={p.id + "-" + p.version + "-" + rev}
                          p={p}
                          identity={identity}
                          onChange={refresh}
                          currentVersion={c.version}
                          onResubmit={() => {
                            setProposal(p.action);
                            setReason(p.reason);
                            setTab("Actions");
                          }}
                        />
                      ))
                    ) : (
                      <Empty title="No proposed actions" />
                    )
                  }
                />
              </div>
            </Panel>
            {identity.role === "analyst" && open && (
              <Panel title="Submit your recommendation">
                <form
                  className="panel-body"
                  onSubmit={(e) => {
                    e.preventDefault();
                    action.run(
                      () =>
                        mutate("/cases/" + id + "/action-proposals", {
                          ...version,
                          action: proposal,
                          reason,
                        }),
                      "Proposal submitted for independent review.",
                    );
                  }}
                >
                  <p>
                    Supervisor review is required for every recommendation,
                    including no action.
                  </p>
                  {data.investigation?.state !== "ACTIVE" && (
                    <p className="notice warning">
                      Begin or resume the investigation in Overview before
                      submitting your recommendation.
                    </p>
                  )}
                  {data.pending_proposals > 0 && (
                    <p className="notice">
                      Your recommendation is awaiting supervisor review. It will
                      not expire automatically.
                    </p>
                  )}
                  <fieldset
                    disabled={
                      action.busy ||
                      data.investigation?.state !== "ACTIVE" ||
                      data.pending_proposals > 0 ||
                      !!data.active_call ||
                      data.unfinished_approved_actions > 0
                    }
                  >
                    <Field label="Recommended action">
                      <select
                        value={proposal}
                        onChange={(e) => setProposal(e.target.value)}
                      >
                        {[
                          "HOLD_TRANSFER",
                          "BLOCK_CARD",
                          "FREEZE_DIGITAL_ACCESS",
                          "FREEZE_ACCOUNT",
                          "STEP_UP_AUTH",
                          "NO_ACTION",
                        ].map((x) => (
                          <option key={x} value={x}>
                            {label(x)}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Reason and supporting evidence">
                      <textarea
                        required
                        minLength={5}
                        maxLength={2000}
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                      />
                    </Field>
                    <button>Submit to supervisor</button>
                  </fieldset>
                  <p className="muted">
                    Finish active calls first. The backend binds the action to
                    this transaction and the current evidence.
                  </p>
                </form>
              </Panel>
            )}
          </div>
        )}
        {tab === "Timeline" && (
          <Panel title="Case activity">
            <div className="panel-body">
              <Collection<Event>
                path={"/cases/" + id + "/events"}
                revision={rev}
                render={(items) => (
                  <ol className="timeline">
                    {items.map((e) => (
                      <li key={e.id}>
                        <strong>{label(e.kind.replaceAll(".", "_"))}</strong>
                        <small>{date(e.created_at)}</small>
                        <Details value={e.details} />
                      </li>
                    ))}
                  </ol>
                )}
              />
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}
function Evidence({
  id,
  revision,
  transaction,
}: {
  id: string;
  revision: number;
  transaction: Transaction;
}) {
  const [before, setBefore] = useState("");
  const evidence = useResource<{
    history: Transaction[];
    next_cursor: string | null;
    as_of: string;
  }>(
    "/cases/" + id + "/evidence?limit=20" + (before ? "&before=" + before : ""),
    revision,
  );
  return (
    <div className="case-columns">
      <Panel title="Account history">
        <div className="panel-body">
          <ErrorBox error={evidence.error} />
          {evidence.loading ? (
            <Loading />
          ) : (
            <>
              <p className="muted">
                Evidence available at {date(evidence.data?.as_of)}. Later
                transactions are excluded.
              </p>
              {!evidence.data?.history?.length && !evidence.error && (
                <p>
                  No earlier payments were recorded in this evidence snapshot.
                </p>
              )}
              {evidence.data?.history?.map((t) => (
                <div className="history-row" key={t.id}>
                  <div>
                    <strong>{t.external_id}</strong>
                    <small>
                      {date(t.occurred_at)}, {label(t.channel)}
                    </small>
                  </div>
                  <strong>{money(t.amount_minor, t.currency)}</strong>
                </div>
              ))}
              <div className="button-row">
                {before && (
                  <button className="text-button" onClick={() => setBefore("")}>
                    First page
                  </button>
                )}
                {evidence.data?.next_cursor && (
                  <button
                    className="secondary"
                    onClick={() => setBefore(evidence.data!.next_cursor!)}
                  >
                    Older transactions
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </Panel>
      <div>
        <Panel title="Scoring evidence">
          <div className="panel-body">
            <ScoringEvidence transaction={transaction} />
          </div>
        </Panel>
        <Panel title="Feature snapshot">
          <div className="panel-body">
            {transaction.features ? (
              <ReadableEvidence value={transaction.features} />
            ) : (
              <p>No feature snapshot has been recorded.</p>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
