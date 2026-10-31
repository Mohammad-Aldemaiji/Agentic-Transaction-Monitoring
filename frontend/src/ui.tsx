import { useEffect, useState, type ReactNode } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { api, ApiError, label } from "./api";
export function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <ShieldCheck size={25} />
      </span>
      <span>
        rasid
        <span className="brand-ar" lang="ar">
          راصد
        </span>
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
