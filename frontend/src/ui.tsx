import { useEffect, useState, type ReactNode } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { api, ApiError, label } from "./api";
export type Theme = "light" | "dark";
const THEME_KEY = "rasid.theme";
/** The saved theme, else the system preference. Storage may be unavailable. */
export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* private mode or blocked storage: fall through */
  }
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#0a0b24" : "#eef0f8");
}
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(
    () => (document.documentElement.dataset.theme as Theme) || initialTheme(),
  );
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  const toggle = () =>
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        /* the choice still applies for this visit */
      }
      return next;
    });
  return { theme, toggle };
}
/** Radar mark: a ring, a rotating sweep needle and a contact dot. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg
      className="brand-logo"
      width={size}
      height={size}
      viewBox="0 0 48 48"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="rasid-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#00c2ff" />
          <stop offset="1" stopColor="#8b3ffc" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="14" fill="url(#rasid-mark)" />
      <circle
        cx="24"
        cy="24"
        r="13"
        fill="none"
        stroke="#fff"
        strokeWidth="2.8"
      />
      <g className="sweep">
        <path
          d="M24 24 33 15"
          stroke="#fff"
          strokeWidth="2.8"
          strokeLinecap="round"
        />
        <circle cx="34.5" cy="13.5" r="3.6" fill="#fff" />
      </g>
      <circle cx="24" cy="24" r="2.8" fill="#fff" />
    </svg>
  );
}
export function Brand() {
  return (
    <div className="brand">
      <Logo />
      <span>Rasid</span>
      <span className="brand-ar" lang="ar">
        راصد
      </span>
    </div>
  );
}
export function Badge({ value }: { value: string }) {
  return <span className={"badge " + value.toLowerCase()}>{label(value)}</span>;
}
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <AlertCircle size={19} />
      <div>
        {error instanceof Error ? error.message : "Something went wrong."}
        {error instanceof ApiError && error.requestId && (
          <small>Reference: {error.requestId}</small>
        )}
      </div>
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <CheckCircle2 size={30} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={22} /> Loading workspace…
    </div>
  );
}
export function Title({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      <div className="heading-actions">{actions}</div>
    </div>
  );
}
export function Refresh({ onClick }: { onClick: () => void }) {
  return (
    <button className="secondary icon-label" onClick={onClick}>
      <RefreshCw size={15} />
      Refresh
    </button>
  );
}
export function Next({ onClick }: { onClick: () => void }) {
  return (
    <button className="secondary icon-label" onClick={onClick}>
      Next page
      <ArrowRight size={16} />
    </button>
  );
}
export function Panel({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}
export function Field({
  label: caption,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{caption}</span>
      {children}
    </label>
  );
}
export function Details({ value }: { value: unknown }) {
  return <pre className="data-view">{JSON.stringify(value, null, 2)}</pre>;
}
export function useResource<T>(path: string | null, revision = 0) {
  const [result, setResult] = useState<{ path: string; data: T } | null>(null),
    [error, setError] = useState<unknown>(null),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    setError(null);
    if (!path) return;
    const controller = new AbortController();
    setLoading(true);
    api<T>(path, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResult({ path, data });
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, revision]);
  return { data: result?.path === path ? result.data : null, error, loading };
}
/** Ticks every `ms` while the page is visible, and once when it becomes visible again. */
export function useLive(ms = 5000) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => {
      if (document.visibilityState !== "hidden") setTick((t) => t + 1);
    };
    const timer = setInterval(bump, ms);
    document.addEventListener("visibilitychange", bump);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", bump);
    };
  }, [ms]);
  return tick;
}
export function useAction(onSuccess?: () => void) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null),
    [success, setSuccess] = useState("");
  async function run(
    work: () => Promise<unknown>,
    message = "Saved successfully.",
  ) {
    if (busy) return false;
    setBusy(true);
    setError(null);
    setSuccess("");
    try {
      await work();
      setSuccess(message);
      onSuccess?.();
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  }
  return {
    busy,
    error,
    success,
    run,
    feedback: (
      <>
        <ErrorBox error={error} />
        {success && (
          <div className="notice success" role="status">
            <CheckCircle2 size={18} />
            {success}
          </div>
        )}
      </>
    ),
  };
}
