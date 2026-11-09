import { useEffect, useRef, useState, type ElementType } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Flag,
  Gauge,
  Hourglass,
  Search,
  X,
} from "lucide-react";
import { label, money, short } from "./api";
import {
  Donut,
  FlaggedBars,
  SEVERITY_COLOR,
  ScoreHistogram,
  Sparkline,
  useBump,
  useCountUp,
} from "./Charts";
import {
  Badge,
  Empty,
  ErrorBox,
  Loading,
  Next,
  Panel,
  Refresh,
  useLive,
  useResource,
} from "./ui";
import type { Case, Dashboard, Page } from "./types";
export const LIVE_MS = 5000;
const count = new Intl.NumberFormat("en-GB");
const percent = new Intl.NumberFormat("en-GB", {
  style: "percent",
  maximumFractionDigits: 1,
});
const clock = new Intl.DateTimeFormat("en-GB", { timeStyle: "medium" });
const today = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
});
export function ago(value: string | null | undefined, now = Date.now()) {
  if (!value) return null;
  const minutes = Math.max(0, Math.floor((now - Date.parse(value)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.floor(hours / 24)} days ago`;
}
/** Plain waiting time, e.g. "15 days", without "ago". */
const waiting = (value: string) => ago(value)?.replace(" ago", "") ?? "";
const OPEN_STATES = ["OPEN", "IN_REVIEW", "AWAITING_CUSTOMER"];
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);

/** One number card. The value briefly highlights when a live update changes it. */
function Stat({
  title,
  value,
  hint,
  icon: Icon,
}: {
  title: string;
  value: number | string;
  hint: string;
  icon: ElementType;
}) {
  const previous = useRef(value);
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    if (previous.current === value) return;
    const first = previous.current === "—";
    previous.current = value;
    if (first) return;
    setChanged(true);
    const timer = setTimeout(() => setChanged(false), 1400);
    return () => clearTimeout(timer);
  }, [value]);
  return (
    <div className={"stat" + (changed ? " changed" : "")}>
      <div>
        <span>{title}</span>
        <Icon size={18} />
      </div>
      <strong>{typeof value === "number" ? count.format(value) : value}</strong>
      <small>{hint}</small>
    </div>
  );
}

function Kpi({
  title,
  value,
  digits = 0,
  unit = "",
  note,
  tone = "",
  series,
  color,
  fill,
}: {
  title: string;
  value: number | null;
  digits?: number;
  unit?: string;
  note: string;
  tone?: "" | "good" | "up-bad";
  series?: (number | null)[];
  color: string;
  fill: string;
}) {
  const shown = useCountUp(value ?? 0);
  const bumped = useBump(value);
  return (
    <section className="kpi" aria-label={title}>
      <div>
        <div className="kpi-label">{title}</div>
        <div className={"kpi-value" + (bumped ? " bumped" : "")}>
          {value === null ? "—" : shown.toFixed(digits) + unit}
        </div>
        <div className={"kpi-note " + tone}>{note}</div>
      </div>
      {series && (
        <Sparkline
          values={series}
          color={color}
          fill={fill}
          label={title + ", last 7 days"}
        />
      )}
    </section>
  );
}

function compare(todayValue: number, yesterday: number) {
  const d = todayValue - yesterday;
  if (d === 0) return "Same as yesterday";
  return `${d > 0 ? "▲" : "▼"} ${Math.abs(d)} ${d > 0 ? "more" : "fewer"} than yesterday`;
}

/** The single most urgent open case, front and centre. */
function Hero({
  cases,
  me,
  openCase,
  loading,
}: {
  cases: Case[];
  me?: string;
  openCase: (id: string) => void;
  loading: boolean;
}) {
  const [index, setIndex] = useState(0);
  const c = cases[index % Math.max(1, cases.length)];
  const score = useCountUp(c?.risk_score ?? 0);
  if (!c)
    return (
      <section className="hero" aria-label="Next up">
        <span className="hero-ring" aria-hidden="true" />
        <span className="hero-sweep" aria-hidden="true" />
        <div className="hero-label">Start here</div>
        <div className="hero-calm">
          {loading
            ? "Looking for the most urgent case…"
            : "All clear. There are no open cases in your queue."}
        </div>
        <p style={{ margin: 0, color: "#e0e7ff" }}>
          New alerts appear here as soon as a payment is flagged.
        </p>
      </section>
    );
  const sev = c.severity.toLowerCase();
  const owner = c.assigned_to
    ? c.assigned_to === me
      ? "Assigned to you"
      : "Assigned"
    : "Nobody assigned yet";
  const [prefix, rest] = c.account_id.includes("-")
    ? [
        c.account_id.slice(0, c.account_id.lastIndexOf("-") + 1),
        c.account_id.slice(c.account_id.lastIndexOf("-") + 1),
      ]
    : ["", c.account_id];
  return (
    <section className="hero" aria-label="Next up">
      <span className="hero-ring" aria-hidden="true" />
      <span className="hero-sweep" aria-hidden="true" />
      <span className="hero-sheen" aria-hidden="true" />
      {[
        [64, 70, 0, ""],
        [138, 26, 1.3, ""],
        [36, 150, 2.6, " red"],
      ].map(([r, t, d, red]) => (
        <span
          key={String(r)}
          className={"blip" + red}
          aria-hidden="true"
          style={{ right: r + "px", top: t + "px", animationDelay: d + "s" }}
        />
      ))}
      {[
        [6, 3, 7, 0],
        [18, 4, 9, 2.2],
        [31, 2, 6, 4.1],
        [44, 3, 8, 1.1],
        [57, 5, 10, 3.3],
        [69, 2, 7, 5.2],
        [81, 4, 9, 0.6],
        [93, 3, 8, 2.9],
      ].map(([l, s, dur, d]) => (
        <span
          key={l}
          className="spark"
          aria-hidden="true"
          style={{
            left: l + "%",
            width: s,
            height: s,
            animationDuration: dur + "s",
            animationDelay: d + "s",
          }}
        />
      ))}
      <div className="hero-label">
        {index === 0
          ? "Start here: the most urgent open case"
          : `Next case (${(index % cases.length) + 1} of ${cases.length})`}
      </div>
      <div className="hero-main">
        <span
          className="score-tile"
          style={{
            ["--tile-bar" as string]:
              SEVERITY_COLOR[c.severity] ?? SEVERITY_COLOR.UNSCORED,
          }}
          aria-label={
            c.risk_score === null
              ? "Not scored"
              : `Risk score ${Math.round(c.risk_score)} of 100`
          }
        >
          <strong>{c.risk_score === null ? "—" : Math.round(score)}</strong>
          <small>of 100</small>
        </span>
        <div className="hero-who">
          <div className="account">
            <span>{prefix}</span>
            {rest}
          </div>
          <p>
            {money(c.amount_minor, c.currency)} {label(c.channel).toLowerCase()}
            , waiting for <b>{waiting(c.created_at)}</b>
          </p>
        </div>
        <div className="hero-tags">
          <span className={"sev-pill " + sev}>{label(c.severity)}</span>
          <span className="glass-tag">{label(c.state)}</span>
          <span className="glass-tag">{owner}</span>
        </div>
      </div>
      <div className="hero-actions">
        <button className="solid" onClick={() => openCase(c.id)}>
          Open case{" "}
          <span className="nudge" aria-hidden="true">
            →
          </span>
        </button>
        {cases.length > 1 && (
          <button className="glass-cta" onClick={() => setIndex((i) => i + 1)}>
            Show next case
          </button>
        )}
      </div>
    </section>
  );
}

export default function Queue({
  openCase,
  me,
}: {
  openCase: (id: string) => void;
  me?: string;
}) {
  const [revision, setRevision] = useState(0),
    [state, setState] = useState(""),
    [severity, setSeverity] = useState(""),
    [assignment, setAssignment] = useState("all"),
    [order, setOrder] = useState("risk"),
    [account, setAccount] = useState(""),
    [search, setSearch] = useState(""),
    [cursor, setCursor] = useState(""),
    [arrived, setArrived] = useState(0);
  const params = new URLSearchParams({ limit: "20", order, assignment });
  if (state) params.set("state", state);
  if (severity) params.set("severity", severity);
  if (account) params.set("account_id", account);
  if (cursor) params.set("after", cursor);
  // The numbers and charts refresh themselves; the queue list changes only on Refresh,
  // so rows never move while someone is reading them.
  const tick = useLive(LIVE_MS);
  const queue = useResource<Page<Case>>("/cases?" + params, revision),
    urgent = useResource<Page<Case>>(
      "/cases?limit=10&order=risk&assignment=all",
      revision,
    ),
    dashboard = useResource<Dashboard>("/dashboard", revision + tick);
  const stats = dashboard.data;
  const traffic = stats?.transactions;
  const trends = stats?.trends;
  const change = (f: (v: string) => void, v: string) => {
    f(v);
    setCursor("");
  };
  const refresh = () => {
    setArrived(0);
    setRevision((x) => x + 1);
  };

  // New alerts: when the case total grows between live updates, say so (and offer a refresh).
  const allCases = stats ? sum(Object.values(stats.cases)) : null;
  const lastTotal = useRef<number | null>(null);
  useEffect(() => {
    if (allCases === null) return;
    const before = lastTotal.current;
    if (before !== null && allCases > before)
      setArrived((n) => n + (allCases - before));
    lastTotal.current = allCases;
  }, [allCases]);
  useEffect(() => {
    if (!arrived) return;
    const timer = setTimeout(() => setArrived(0), 6000);
    return () => clearTimeout(timer);
  }, [arrived]);

  // Rows that were not in the previous load glow briefly.
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    const ids = queue.data?.items.map((c) => c.id);
    if (!ids) return;
    if (seen.current)
      setFresh(new Set(ids.filter((id) => !seen.current!.has(id))));
    seen.current = new Set([...(seen.current ?? []), ...ids]);
  }, [queue.data]);

  const openCount = stats
    ? sum(OPEN_STATES.map((s) => stats.cases[s] ?? 0))
    : null;
  const flaggedSeries = trends?.flagged_by_day.map((d) =>
    sum(Object.values(d)),
  );
  const flaggedToday = flaggedSeries?.[flaggedSeries.length - 1] ?? null;
  const flaggedYesterday = flaggedSeries?.[flaggedSeries.length - 2] ?? 0;
  const decidedSeries = trends?.decided_by_day;
  const decidedYesterday = decidedSeries?.[decidedSeries.length - 2] ?? 0;
  const hours = trends?.decision_hours_by_day ?? [];
  const weighted = hours.reduce<[number, number]>(
    (acc, h, i) =>
      h === null
        ? acc
        : [
            acc[0] + h * (decidedSeries?.[i] ?? 1),
            acc[1] + (decidedSeries?.[i] ?? 1),
          ],
    [0, 0],
  );
  const avgHours = weighted[1] ? weighted[0] / weighted[1] : null;
  const decisionTotal = stats ? sum(Object.values(stats.decisions)) : 0;
  const fraudShare = decisionTotal
    ? ((stats?.decisions.CONFIRMED_FRAUD ?? 0) / decisionTotal) * 100
    : null;
  const urgentOpen = (urgent.data?.items ?? []).filter((c) =>
    OPEN_STATES.includes(c.state),
  );
  const severityCounts = stats?.open_severity ?? {};
  return (
    <>
      <div className="ov-heading">
        <h1>Overview</h1>
        <span className="live-note" aria-live="off">
          <span className="live-dot" />
          {stats?.generated_at
            ? "Live, updated " + clock.format(new Date(stats.generated_at))
            : "Connecting…"}
        </span>
        <span className="muted">{today.format(new Date())}</span>
        <div className="heading-actions">
          <Refresh onClick={refresh} />
        </div>
      </div>
      <ErrorBox error={dashboard.error} />

      <div className="ov-row">
        <Hero
          cases={urgentOpen}
          me={me}
          openCase={openCase}
          loading={urgent.loading}
        />
        <section
          className="card donut-card"
          aria-label="Open cases by priority"
        >
          <h2>Open cases by priority</h2>
          {stats ? <Donut counts={severityCounts} /> : <Loading />}
        </section>
      </div>

      <div className="kpis">
        <Kpi
          title="Open cases"
          value={openCount}
          note={
            stats?.oldest_open_at
              ? "Oldest waiting " + waiting(stats.oldest_open_at)
              : "Nothing waiting"
          }
          color="#7c3aed"
          fill="rgba(124,58,237,.12)"
        />
        <Kpi
          title="Flagged today"
          value={flaggedToday}
          note={
            flaggedToday === null
              ? "Daily history unavailable"
              : compare(flaggedToday, flaggedYesterday)
          }
          tone={
            flaggedToday !== null && flaggedToday > flaggedYesterday
              ? "up-bad"
              : ""
          }
          series={flaggedSeries?.slice(-7)}
          color="#c026d3"
          fill="rgba(192,38,211,.10)"
        />
        <Kpi
          title="Decided today"
          value={stats?.decided_today ?? null}
          note={
            decidedSeries && stats?.decided_today !== undefined
              ? compare(stats.decided_today, decidedYesterday)
              : "Decisions recorded today"
          }
          tone={
            decidedSeries &&
            stats?.decided_today !== undefined &&
            stats.decided_today >= decidedYesterday
              ? "good"
              : ""
          }
          series={decidedSeries}
          color="#0891b2"
          fill="rgba(8,145,178,.12)"
        />
        <Kpi
          title={
            avgHours !== null ? "Average time to decide" : "Confirmed fraud"
          }
          value={avgHours !== null ? avgHours : fraudShare}
          digits={1}
          unit={avgHours !== null ? " h" : "%"}
          note={
            avgHours !== null
              ? "From alert to decision, last 7 days"
              : `Of ${decisionTotal} decided cases`
          }
          series={avgHours !== null ? hours : undefined}
          color="#7c3aed"
          fill="rgba(124,58,237,.12)"
        />
      </div>

      <div className="ov-row">
        <section
          className="card chart-card wide"
          aria-label="Cases flagged per day"
        >
          {trends ? (
            <FlaggedBars days={trends.days} series={trends.flagged_by_day} />
          ) : (
            <>
              <h2>Cases flagged per day</h2>
              <div className="chart-empty">
                {stats
                  ? "Daily history is not available from this server."
                  : "Loading…"}
              </div>
            </>
          )}
        </section>
        <section className="card chart-card" aria-label="Risk scores today">
          {traffic?.score_bins_today ? (
            <ScoreHistogram
              bins={traffic.score_bins_today}
              threshold={stats?.review_threshold ?? null}
            />
          ) : (
            <>
              <h2>How cases were decided</h2>
              <DecisionBars decisions={stats?.decisions ?? {}} />
            </>
          )}
        </section>
      </div>

      {traffic && (
        <>
          <h2 className="stats-title">Live transaction flow</h2>
          <div className="stats-grid">
            {[
              {
                title: "Transactions received",
                value: traffic.received,
                hint:
                  `${count.format(traffic.received_last_hour)} in the last hour` +
                  (traffic.last_received_at
                    ? `, latest ${ago(traffic.last_received_at)}`
                    : ""),
                icon: ArrowDownToLine,
              },
              {
                title: "Flagged for review",
                value: traffic.flagged,
                hint:
                  traffic.flag_rate === null
                    ? "No transactions yet"
                    : `${percent.format(traffic.flag_rate)} of received, ${count.format(traffic.flagged_last_hour)} in the last hour`,
                icon: Flag,
              },
              {
                title: "Average risk score",
                value:
                  traffic.average_risk_score === null
                    ? "—"
                    : traffic.average_risk_score.toFixed(2),
                hint: "Model output times 100. Not a probability.",
                icon: Gauge,
              },
              {
                title: "Waiting for scoring",
                value: traffic.pending,
                hint:
                  `${count.format(traffic.scored)} scored` +
                  (traffic.failed
                    ? `, ${count.format(traffic.failed)} failed`
                    : ""),
                icon: Hourglass,
              },
            ].map((s) => (
              <Stat key={s.title} {...s} />
            ))}
          </div>
        </>
      )}

      <Panel
        title="Review queue"
        aside={
          <span className="muted">
            {queue.data ? `${queue.data.items.length} on this page` : ""}
          </span>
        }
      >
        <div className="filters">
          <div
            className="pill-group"
            role="group"
            aria-label="Filter by priority"
          >
            {[
              ["", "All"],
              ["CRITICAL", "Critical"],
              ["HIGH", "High"],
              ["MEDIUM", "Medium"],
              ["LOW", "Low"],
              ["UNSCORED", "Unscored"],
            ].map(([value, name]) => (
              <button
                key={name}
                aria-pressed={severity === value}
                onClick={() => change(setSeverity, value)}
              >
                {name}
              </button>
            ))}
          </div>
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
              placeholder="Search by exact account ID"
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
            <option value="OPEN">Open</option>
            <option value="IN_REVIEW">In review</option>
            <option value="AWAITING_CUSTOMER">Awaiting customer</option>
            <option value="RESOLVED">Resolved</option>
          </select>
          <select
            aria-label="Assignment"
            value={assignment}
            onChange={(e) => change(setAssignment, e.target.value)}
          >
            <option value="all">Everyone</option>
            <option value="mine">Assigned to me</option>
            <option value="unassigned">Nobody yet</option>
          </select>
          <select
            aria-label="Sort cases"
            value={order}
            onChange={(e) => change(setOrder, e.target.value)}
          >
            <option value="risk">Highest risk first</option>
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
                  <th>Risk score</th>
                  <th>Account</th>
                  <th style={{ textAlign: "right" }}>Amount</th>
                  <th>Status</th>
                  <th>Assigned to</th>
                  <th>Waiting</th>
                  <th>
                    <span className="sr-only">Open case</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {queue.data.items.map((c) => {
                  const mine = c.assigned_to === me && !!me;
                  return (
                    <tr key={c.id} className={fresh.has(c.id) ? "fresh" : ""}>
                      <td>
                        <div className="score-cell">
                          <span
                            className="score"
                            title={
                              c.risk_score === null
                                ? undefined
                                : c.risk_score.toFixed(2)
                            }
                          >
                            {c.risk_score === null
                              ? "—"
                              : Math.round(c.risk_score)}
                          </span>
                          <Badge value={c.severity} />
                        </div>
                      </td>
                      <td>
                        <strong>{c.account_id}</strong>
                        <small>
                          <button
                            className="record-link"
                            onClick={() => openCase(c.id)}
                          >
                            CASE-{short(c.id)}
                          </button>
                        </small>
                      </td>
                      <td className="amount">
                        {money(c.amount_minor, c.currency)}
                        <small>{label(c.channel)}</small>
                      </td>
                      <td>
                        <Badge value={c.state} />
                      </td>
                      <td className="muted">
                        {c.assigned_to
                          ? mine
                            ? "You"
                            : short(c.assigned_to)
                          : "Nobody yet"}
                      </td>
                      <td className="muted">{waiting(c.created_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          className={
                            "row-action" +
                            (c.state === "RESOLVED" || (c.assigned_to && !mine)
                              ? " quiet"
                              : "")
                          }
                          aria-label={"Open case " + short(c.id)}
                          onClick={() => openCase(c.id)}
                        >
                          {c.state === "RESOLVED"
                            ? "View"
                            : mine
                              ? "Continue"
                              : c.assigned_to
                                ? "View"
                                : "Open"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          !queue.error && (
            <Empty title="No cases match this view">
              New cases appear here when a payment needs review. Try another
              filter, or press Refresh.
            </Empty>
          )
        )}
        <div className="table-footer">
          <span>
            Flagged payments only. The list changes when you press Refresh, so
            rows never move while you read.
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

      {stats?.review_capacity && (
        <Panel title="Team review capacity">
          <div className="capacity">
            <strong>
              {stats.review_capacity.completed_today}
              <small>
                {" "}
                of {stats.review_capacity.target ?? "no target"} reviews done
                today
              </small>
            </strong>
            <p>
              {stats.review_capacity.opened_today} cases opened today.{" "}
              {stats.review_capacity.over_target
                ? "That is above the review target. Every alert still stays visible."
                : "Targets never hide alerts."}
            </p>
          </div>
        </Panel>
      )}

      {arrived > 0 && (
        <div className="toast" role="status" aria-live="polite">
          <span className="toast-icon" aria-hidden="true">
            <i />
          </span>
          <div>
            <strong>
              {arrived === 1
                ? "New case flagged"
                : `${arrived} new cases flagged`}
            </strong>
            <p>
              The charts are up to date. Refresh the list to see{" "}
              {arrived === 1 ? "it" : "them"}.
            </p>
            <button
              className="text-button"
              style={{ color: "#c4b5fd" }}
              onClick={refresh}
            >
              Refresh the list <ArrowRight size={14} />
            </button>
          </div>
          <button
            className="toast-close"
            aria-label="Dismiss"
            onClick={() => setArrived(0)}
          >
            <X size={14} />
          </button>
          <span className="timer" aria-hidden="true" />
        </div>
      )}
    </>
  );
}

function DecisionBars({ decisions }: { decisions: Record<string, number> }) {
  const rows = [
    ["LEGITIMATE", "Legitimate", "#16a34a"],
    ["CONFIRMED_FRAUD", "Confirmed fraud", "#d92d20"],
    ["INCONCLUSIVE", "Inconclusive", "#a3a9c2"],
  ] as const;
  const total = sum(rows.map(([k]) => decisions[k] ?? 0));
  if (!total)
    return (
      <div className="chart-empty">No decisions have been recorded yet.</div>
    );
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        marginTop: 8,
      }}
    >
      {rows.map(([key, name, color], i) => {
        const n = decisions[key] ?? 0;
        return (
          <div key={key}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                marginBottom: 6,
              }}
            >
              <span>{name}</span>
              <b>
                {n}{" "}
                <span className="muted" style={{ fontWeight: 500 }}>
                  ({Math.round((n / total) * 100)}%)
                </span>
              </b>
            </div>
            <div
              style={{
                height: 10,
                borderRadius: 99,
                background: "var(--track)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: (n / total) * 100 + "%",
                  height: "100%",
                  borderRadius: 99,
                  background: color,
                  transformOrigin: "left",
                  animation: `grow-x .8s ${0.3 + i * 0.1}s cubic-bezier(.2,.7,.2,1) both`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
