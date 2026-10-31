const CSRF_KEY = "rasid.csrf";
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public requestId?: string,
  ) {
    super(errorText(status, code));
  }
}
export function errorText(status: number, code: string) {
  const messages: Record<string, string> = {
    local_agent_disabled: "Local AI has not been enabled for this workspace.",
    local_agent_unavailable:
      "Local AI is unavailable or busy. You can continue reviewing the case manually.",
    local_transcription_disabled:
      "Local speech recognition has not been configured.",
    local_transcription_unavailable:
      "This recording could not be transcribed. You can type your reply instead.",
    fresh_authentication_required:
      "Please sign in again with your authenticator, then retry this approval.",
    independent_approval_required:
      "A different supervisor must approve this request.",
    identity_inactive:
      "Your account is not enabled for this workspace. Ask the project administrator to grant access.",
    authentication_provider_unavailable:
      "Sign-in verification is temporarily unavailable. Please try again shortly.",
    mfa_required:
      "Complete authenticator verification to access this workspace.",
    evidence_changed:
      "The case evidence changed. Refresh and review it before creating a new proposal.",
    csrf_failed:
      "Your browser session changed. Sign in again before making changes.",
    rate_limit_exceeded:
      "Too many requests. Wait a minute before trying again.",
    scoring_disabled:
      "Scoring is unavailable. This transaction needs manual review.",
  };
  return (
    messages[code] ??
    (status === 401
      ? "Your session has expired. Please sign in again."
      : status === 403
        ? "Your role cannot perform this action."
        : status === 404
          ? "This record is unavailable or outside your access."
          : status === 409
            ? "This record changed or the action is not allowed in its current state. Refresh and review before retrying."
            : status === 422
              ? "Please check the entered values."
              : "The request could not be confirmed. Refresh before retrying; a change may already have been saved.")
  );
}
export function clearSession() {
  sessionStorage.removeItem(CSRF_KEY);
}
export function hasSession() {
  return !!sessionStorage.getItem(CSRF_KEY);
}
export function rememberSession(token: string) {
  sessionStorage.setItem(CSRF_KEY, token);
}
export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    bearer?: string;
    idempotency?: string;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  if (!path.startsWith("/") || path.startsWith("//"))
    throw new Error("Invalid API path");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.bearer) headers.Authorization = `Bearer ${options.bearer}`;
  const csrf = sessionStorage.getItem(CSRF_KEY);
  if (csrf && options.method && options.method !== "GET")
    headers["X-CSRF-Token"] = csrf;
  if (options.idempotency) headers["Idempotency-Key"] = options.idempotency;
  let res: Response;
  try {
    res = await fetch("/api/v1" + path, {
      method: options.method ?? "GET",
      headers,
      credentials: "same-origin",
      cache: "no-store",
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal ?? AbortSignal.timeout(25000),
    });
  } catch (e) {
    if (options.signal?.aborted) throw e;
    throw new ApiError(0, "network_error");
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    if (res.status === 401 && !options.bearer) {
      clearSession();
      window.dispatchEvent(new Event("session-expired"));
    }
    throw new ApiError(
      res.status,
      payload.error?.code ?? "request_failed",
      payload.error?.request_id,
    );
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}
export const label = (value: string) =>
  value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
export const short = (value: string) => value.slice(0, 8).toUpperCase();
export const date = (value?: string | null) =>
  value
    ? new Intl.DateTimeFormat("en-GB", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
// The API uses minor units, including three decimals for KWD/BHD/OMR.
export const decimals = (currency: string) =>
  ["KWD", "BHD", "OMR"].includes(currency) ? 3 : 2;
export const money = (amount: number, currency: string) =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(
    amount / 10 ** decimals(currency),
  );
export function minorUnits(value: string, currency: string) {
  const d = decimals(currency);
  const pattern = new RegExp(`^\\d+(?:\\.\\d{1,${d}})?$`);
  if (!pattern.test(value))
    throw new Error(
      `Enter a positive amount with at most ${d} decimal places.`,
    );
  const [whole, fraction = ""] = value.split(".");
  const amount =
    BigInt(whole) * 10n ** BigInt(d) + BigInt(fraction.padEnd(d, "0"));
  if (amount <= 0n || amount > 1000000000000n)
    throw new Error("Amount is outside the supported range.");
  return Number(amount);
}
