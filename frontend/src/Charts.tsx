import { useEffect, useRef, useState } from "react";
import { label } from "./api";

/** Severity colours shared by every chart and legend. */
export const SEVERITY = [
  "CRITICAL",
  "HIGH",
  "MEDIUM",
  "LOW",
  "UNSCORED",
] as const;
export const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: "#d92d20",
  HIGH: "#f79009",
  MEDIUM: "#fdd535",
  LOW: "#a3a9c2",
  UNSCORED: "#6b6f96",
};
const reduceMotion = () => {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
};
/** Eases from the previous value to the new one; jumps straight there for reduced motion. */
export function useCountUp(target: number, ms = 1000) {
  const [value, setValue] = useState(() => (reduceMotion() ? target : 0));
  const from = useRef(value);
  useEffect(() => {
    if (reduceMotion() || typeof requestAnimationFrame !== "function") {
      from.current = target;
      setValue(target);
      return;
    }
    const start = from.current;
    let frame = 0,
      began: number | null = null;
    const step = (now: number) => {
      began ??= now;
      const t = Math.min(1, (now - began) / ms);
      const next = start + (target - start) * (1 - Math.pow(1 - t, 3));
      from.current = next;
      setValue(next);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, ms]);
  return value;
}
/** True for a moment after `value` changes (not on first render). */
export function useBump(value: unknown, ms = 700) {
  const previous = useRef(value);
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (Object.is(previous.current, value)) return;
    previous.current = value;
    setOn(true);
    const timer = setTimeout(() => setOn(false), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return on;
}

export function Donut({
  counts,
  caption = "open",
}: {
  counts: Record<string, number>;
  caption?: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [drawn, setDrawn] = useState(reduceMotion());
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const rows = SEVERITY.map((k) => [k, counts[k] ?? 0] as const).filter(
    ([k, n]) => n > 0 || k !== "UNSCORED",
  );
  const total = rows.reduce((sum, [, n]) => sum + n, 0);
  const shown = Math.round(useCountUp(total));
  const r = 54,
    C = 2 * Math.PI * r,
    gap = total > 1 ? 2 : 0;
  let offset = 0;
  const segments = rows.map(([key, n]) => {
    const length = total ? (n / total) * C : 0;
    const visible = drawn ? Math.max(length - gap, 0) : 0;
    const seg = { key, n, dash: `${visible} ${C - visible}`, offset: -offset };
    offset += drawn ? length : 0;
    return seg;
  });
  const description =
    `${total} open cases: ` +
    rows.map(([k, n]) => `${n} ${label(k).toLowerCase()}`).join(", ");
  return (
    <div className="donut-wrap">
      <div className="donut">
        <svg
          width="140"
          height="140"
          viewBox="0 0 140 140"
          role="img"
          aria-label={description}
        >
          <circle
            className="orbit"
            cx="70"
            cy="70"
            r="66"
            fill="none"
            stroke="var(--axis)"
            strokeWidth="1.5"
            strokeDasharray="2 7"
          />
          <circle
            cx="70"
            cy="70"
            r={r}
            fill="none"
            stroke="var(--track)"
            strokeWidth="16"
          />
          {segments.map((s) => (
            <circle
              key={s.key}
              className="seg"
              cx="70"
              cy="70"
              r={r}
              fill="none"
              stroke={SEVERITY_COLOR[s.key]}
              strokeWidth={hover === s.key ? 21 : 16}
              opacity={hover && hover !== s.key ? 0.35 : 1}
              strokeDasharray={s.dash}
              strokeDashoffset={s.offset}
              transform="rotate(-90 70 70)"
            >
              <title>{`${label(s.key)}: ${s.n}`}</title>
            </circle>
          ))}
        </svg>
        <div className="donut-center">
          <strong>{shown}</strong>
          <span>{caption}</span>
        </div>
      </div>
      <ul className="legend">
        {rows.map(([key, n]) => (
          <li
            key={key}
            className={hover === key ? "on" : ""}
            onMouseEnter={() => setHover(key)}
            onMouseLeave={() => setHover(null)}
          >
            <span className="key" style={{ background: SEVERITY_COLOR[key] }} />
            <span>{label(key)}</span>
            <b>{n}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Sparkline({
  values,
  color,
  fill,
  label: caption,
}: {
  values: (number | null)[];
  color: string;
  fill: string;
  label: string;
}) {
  const points = values
    .map((v, i) => [i, v] as const)
    .filter((p): p is readonly [number, number] => p[1] !== null);
  if (points.length < 2) return null;
  const w = 100,
    h = 42,
    pad = 5;
  const ys = points.map((p) => p[1]);
  const max = Math.max(...ys),
    min = Math.min(...ys);
  const xy = points.map(([i, v]) => [
    pad + (i * (w - 2 * pad)) / (values.length - 1),
    h - pad - ((v - min) / (max - min || 1)) * (h - 2 * pad),
  ]);
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = xy[xy.length - 1];
  return (
    <svg
      className="spark-svg"
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={caption}
    >
      <polyline
        className="area"
        points={`${xy[0][0]},${h} ${line} ${lx},${h}`}
        fill={fill}
      />
      <polyline
        className="line"
        points={line}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={100}
      />
      <circle
        className="ping"
        cx={lx}
        cy={ly}
        r="3.5"
        fill="none"
        stroke={color}
        strokeWidth="1.5"
      />
      <circle
        cx={lx}
        cy={ly}
        r="3.5"
        fill={color}
        stroke="var(--card)"
        strokeWidth="2"
      />
    </svg>
  );
}

const dayLabel = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});
const niceMax = (n: number) => {
  if (n <= 4) return 4;
  const step = Math.pow(10, Math.floor(Math.log10(n)));
  return Math.ceil(n / step) * step;
};

export function FlaggedBars({
  days,
  series,
}: {
  days: string[];
  series: Record<string, number>[];
}) {
  const [hot, setHot] = useState(-1);
  const bands = SEVERITY.filter(
    (k) => series.some((d) => (d[k] ?? 0) > 0) || k !== "UNSCORED",
  );
  const totals = series.map((d) => bands.reduce((s, k) => s + (d[k] ?? 0), 0));
  const top = niceMax(Math.max(1, ...totals));
  const height = 178;
  const last = series.length - 1;
  const tip = hot >= 0 ? series[hot] : null;
  const summary = `${totals.reduce((a, b) => a + b, 0)} cases flagged in the last ${series.length} days, ${totals[last] ?? 0} today`;
  return (
    <>
      <div className="chart-head">
        <h2>Cases flagged per day</h2>
        <ul className="chart-legend">
          {bands.map((k) => (
            <li key={k}>
              <span className="key" style={{ background: SEVERITY_COLOR[k] }} />
              {label(k)}
            </li>
          ))}
        </ul>
      </div>
      <div className="bars" role="img" aria-label={summary}>
        <div className="y-axis" aria-hidden="true">
          <span>{top}</span>
          <span>{top / 2}</span>
          <span>0</span>
        </div>
        <div className="plot">
          <div className="gridline" style={{ top: 0 }} />
          <div className="gridline" style={{ top: "50%" }} />
          <div className={"columns" + (hot >= 0 ? " dim" : "")}>
            {series.map((d, i) => (
              <div
                key={days[i]}
                className={"column" + (hot === i ? " hot" : "")}
                onMouseEnter={() => setHot(i)}
                onMouseLeave={() => setHot(-1)}
              >
                {bands
                  .filter((k) => (d[k] ?? 0) > 0)
                  .map((k) => (
                    <span
                      key={k}
                      style={{
                        height: ((d[k] / top) * height).toFixed(1) + "px",
                        background: SEVERITY_COLOR[k],
                        animationDelay: (0.3 + i * 0.035).toFixed(3) + "s",
                      }}
                    />
                  ))}
              </div>
            ))}
          </div>
          {totals[last] > 0 && (
            <span
              className="today-dot"
              aria-hidden="true"
              style={{
                right: `calc((100% - ${(series.length - 1) * 6}px) / ${series.length * 2} - 4px)`,
                bottom: (totals[last] / top) * height + 8 + "px",
              }}
            />
          )}
          {tip && (
            <div
              className="chart-tip"
              role="status"
              style={{
                bottom: (totals[hot] / top) * height + 12 + "px",
                left: `clamp(0px, calc(${(((hot + 0.5) / series.length) * 100).toFixed(1)}% - 115px), calc(100% - 230px))`,
              }}
            >
              <b>
                {hot === last ? "Today" : dayLabel.format(new Date(days[hot]))}:
              </b>{" "}
              {totals[hot]} {totals[hot] === 1 ? "case" : "cases"} flagged
              <div>
                {bands.map((k) => `${label(k)}: ${tip[k] ?? 0}`).join(", ")}
              </div>
            </div>
          )}
        </div>
      </div>
      <div className="x-axis" aria-hidden="true">
        {days.map((d, i) => (
          <span key={d} className={i === last ? "today" : ""}>
            {i === last ? "Today" : Number(d.slice(8, 10))}
          </span>
        ))}
      </div>
    </>
  );
}

export function ScoreHistogram({
  bins,
  threshold,
}: {
  bins: number[];
  threshold: number | null;
}) {
  const max = Math.max(1, ...bins);
  const cut = threshold ?? null;
  // Bands are 10 points wide, so the split is shown at the first band edge at or above the threshold.
  const from = cut === null ? null : Math.min(100, Math.ceil(cut / 10) * 10);
  const flagged =
    from === null
      ? 0
      : bins.reduce((s, b, i) => s + (i * 10 >= from ? b : 0), 0);
  const total = bins.reduce((a, b) => a + b, 0);
  if (!total)
    return (
      <>
        <h2>Risk scores today</h2>
        <div className="chart-empty">
          No payments have been scored today yet.
        </div>
      </>
    );
  const pct = cut === null ? null : Math.min(100, Math.max(0, cut));
  return (
    <>
      <h2>Risk scores today</h2>
      <div
        className="hist"
        role="img"
        aria-label={
          `${total} payments scored today` +
          (from === null
            ? ""
            : `, ${flagged} scored ${from} or more; review threshold ${cut}`)
        }
      >
        {bins.map((b, i) => (
          <span
            key={i}
            title={`${i * 10} to ${i * 10 + 9}: ${b} ${b === 1 ? "payment" : "payments"}`}
            className={from !== null && i * 10 >= from ? "flagged" : ""}
            style={{
              height: Math.max(b ? 3 : 0, (b / max) * 170).toFixed(1) + "px",
              animationDelay: (0.3 + i * 0.05).toFixed(2) + "s",
            }}
          />
        ))}
        {pct !== null && (
          <>
            <div
              className="scan"
              style={{ left: pct + "%" }}
              aria-hidden="true"
            >
              <i />
            </div>
            <div
              className="threshold"
              style={{ left: pct + "%" }}
              aria-hidden="true"
            />
            <span
              className="threshold-label"
              style={
                pct > 60
                  ? { right: `calc(${100 - pct}% + 6px)` }
                  : { left: `calc(${pct}% + 6px)` }
              }
            >
              Review threshold ({Math.round(pct)})
            </span>
          </>
        )}
      </div>
      <div className="hist-axis" aria-hidden="true">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>
      {from !== null && (
        <div className="hist-legend">
          <span>
            <span className="key" style={{ background: "var(--hist-off)" }} />
            Under {from}: {total - flagged}
          </span>
          <span>
            <span className="key" style={{ background: "#7c3aed" }} />
            {from} and above: {flagged}
          </span>
        </div>
      )}
    </>
  );
}
