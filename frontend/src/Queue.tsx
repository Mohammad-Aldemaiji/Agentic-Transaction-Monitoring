import { useState } from "react";
import {
  ArrowUpRight,
  Search,
  ShieldAlert,
  Layers3,
  Clock3,
  CircleCheck,
} from "lucide-react";
import { date, money, short } from "./api";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Next,
  Panel,
  Refresh,
  Title,
  useResource,
} from "./ui";
import type { Case, Dashboard, Page } from "./types";
export default function Queue({
  openCase,
}: {
  openCase: (id: string) => void;
}) {
  const [revision, setRevision] = useState(0),
    [state, setState] = useState(""),
    [severity, setSeverity] = useState(""),
    [assignment, setAssignment] = useState("all"),
    [order, setOrder] = useState("risk"),
    [account, setAccount] = useState(""),
    [search, setSearch] = useState(""),
    [cursor, setCursor] = useState("");
  const params = new URLSearchParams({ limit: "20", order, assignment });
  if (state) params.set("state", state);
  if (severity) params.set("severity", severity);
  if (account) params.set("account_id", account);
  if (cursor) params.set("after", cursor);
  const queue = useResource<Page<Case>>("/cases?" + params, revision),
    dashboard = useResource<Dashboard>("/dashboard", revision);
  const stats = dashboard.data;
  const change = (f: (v: string) => void, v: string) => {
    f(v);
    setCursor("");
  };
  return (
    <>
      <Title
        eyebrow="OPERATIONS / OVERVIEW"
        title="Every signal, in focus."
        actions={<Refresh onClick={() => setRevision((x) => x + 1)} />}
      >
        Prioritize what matters. Review the evidence. Keep every decision
        accountable.
      </Title>
      <ErrorBox error={dashboard.error} />
      <div className="stats-grid">
        {[
          {
            title: "Open cases",
            value: stats
              ? (stats.cases.OPEN ?? 0) +
                (stats.cases.IN_REVIEW ?? 0) +
                (stats.cases.AWAITING_CUSTOMER ?? 0)
              : "—",
            hint: "Across your authorized queue",
            icon: Layers3,
          },
          {
            title: "High priority",
            value: stats
              ? (stats.open_severity.HIGH ?? 0) +
                (stats.open_severity.CRITICAL ?? 0)
              : "—",
            hint: "High and critical risk",
            icon: ShieldAlert,
          },
          {
            title: "Awaiting customer",
            value: stats?.cases.AWAITING_CUSTOMER ?? (stats ? 0 : "—"),
            hint: "Verification in progress",
            icon: Clock3,
          },
          {
            title: "Resolved",
            value: stats?.cases.RESOLVED ?? (stats ? 0 : "—"),
            hint: "Reviewed decisions recorded",
            icon: CircleCheck,
          },
        ].map((s) => (
          <div className="stat" key={s.title}>
            <div>
              <span>{s.title}</span>
              <s.icon size={18} />
            </div>
            <strong>{s.value}</strong>
            <small>{s.hint}</small>
          </div>
        ))}
      </div>
      <div className="queue-topline">
        <span className="live-dot" />
        Your authorized case queue
        <span className="muted">Updated on refresh</span>
      </div>
      <Panel
        title="Review queue"
        aside={
          <span className="muted">
            {queue.data?.items.length ?? "—"} on this page
          </span>
        }
      >
        <div className="filters">
          <form
            className="search"
            onSubmit={(e) => {
              e.preventDefault();
              change(setAccount, search.trim());
            }}
          >
            <Search size={17} />
            <input
              aria-label="Exact account ID"
              placeholder="Search exact account ID…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button type="submit" className="text-button">
              Find
            </button>
          </form>
          <select
            aria-label="Case state"
            value={state}
            onChange={(e) => change(setState, e.target.value)}
          >
            <option value="">All states</option>
            <option>OPEN</option>
            <option>IN_REVIEW</option>
            <option>AWAITING_CUSTOMER</option>
            <option>RESOLVED</option>
          </select>
          <select
            aria-label="Severity"
            value={severity}
            onChange={(e) => change(setSeverity, e.target.value)}
          >
            <option value="">All priorities</option>
            {["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNSCORED"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
          <select
            aria-label="Assignment"
            value={assignment}
            onChange={(e) => change(setAssignment, e.target.value)}
          >
            <option value="all">All accessible</option>
            <option value="mine">Assigned to me</option>
            <option value="unassigned">Unassigned</option>
          </select>
          <select
            aria-label="Sort cases"
            value={order}
            onChange={(e) => change(setOrder, e.target.value)}
          >
            <option value="risk">Highest risk</option>
            <option value="oldest">Oldest first</option>
            <option value="newest">Newest first</option>
          </select>
        </div>
        <ErrorBox error={queue.error} />
        {queue.loading ? (
          <Loading />
        ) : queue.data?.items.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case / Account</th>
                  <th>Transaction</th>
                  <th>Priority</th>
                  <th>Risk score</th>
                  <th>Status</th>
                  <th>Opened</th>
                  <th>
                    <span className="sr-only">Open case</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {queue.data.items.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <button
                        className="record-link"
                        onClick={() => openCase(c.id)}
                      >
                        CASE-{short(c.id)}
                      </button>
                      <small>{c.account_id}</small>
                    </td>
                    <td>
                      {money(c.amount_minor, c.currency)}
                      <small>
                        {c.channel} · {c.external_id}
                      </small>
                    </td>
                    <td>
                      <Badge value={c.severity} />
                    </td>
                    <td>
                      <span className="score">
                        {c.risk_score === null
                          ? "Unscored"
                          : c.risk_score.toFixed(2)}
                      </span>
                      {c.risk_score !== null && (
                        <progress
                          max={100}
                          value={c.risk_score}
                          aria-label="Risk score"
                        />
                      )}
                    </td>
                    <td>
                      <Badge value={c.state} />
                    </td>
                    <td className="muted">{date(c.created_at)}</td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={"Open case " + short(c.id)}
                        onClick={() => openCase(c.id)}
                      >
                        <ArrowUpRight size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          !queue.error && (
            <Empty title="No cases match this view">
              New cases will appear here when transactions require review.
              Adjust your filters or refresh the queue.
            </Empty>
          )
        )}
        <div className="table-footer">
          <span>
            This queue contains flagged payments only, sorted by the selected
            order. Use Transactions to see cleared payments and customer
            histories. Only cases permitted by your role and assignment are
            shown.
          </span>
          <div>
            {cursor && (
              <button className="text-button" onClick={() => setCursor("")}>
                First page
              </button>
            )}
            {queue.data?.next_cursor && (
              <Next
                onClick={() => setCursor(String(queue.data!.next_cursor))}
              />
            )}
          </div>
        </div>
      </Panel>
      <div className="bottom-grid">
        <Panel title="Review principles">
          <div className="principles">
            <div>
              <span>01</span>
              <p>
                <strong>Evidence comes first</strong>Read the transaction
                context and account history before acting.
              </p>
            </div>
            <div>
              <span>02</span>
              <p>
                <strong>People make the decision</strong>Customer answers and
                risk scores support your investigation.
              </p>
            </div>
            <div>
              <span>03</span>
              <p>
                <strong>Approval stays independent</strong>High-impact actions
                require a different supervisor.
              </p>
            </div>
          </div>
        </Panel>
        <Panel title="Team review capacity">
          {stats?.review_capacity ? (
            <div className="capacity">
              <strong>
                {stats.review_capacity.completed_today}
                <small> / {stats.review_capacity.target ?? "No target"}</small>
              </strong>
              <p>Reviews completed today (UTC)</p>
              <p>
                {stats.review_capacity.opened_today} cases opened today.{" "}
                {stats.review_capacity.over_target
                  ? "Above the review target; all alerts remain visible."
                  : "Targets never suppress alerts."}
              </p>
            </div>
          ) : (
            <div className="capacity">
              <strong>
                Human-led.
                <br />
                Evidence-backed.
              </strong>
              <p>
                Every action is scoped, versioned, and recorded in the audit
                trail.
              </p>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
