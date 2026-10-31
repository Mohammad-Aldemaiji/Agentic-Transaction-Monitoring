import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Fingerprint,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { supabase, authConfigured } from "./auth";
import { api, ApiError, clearSession, rememberSession } from "./api";
import { Brand, ErrorBox, Field } from "./ui";
import type { Identity } from "./types";
export default function Login({
  onLogin,
  presentation = false,
}: {
  onLogin: (identity: Identity) => void;
  presentation?: boolean;
}) {
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [code, setCode] = useState("");
  const [factor, setFactor] = useState(""),
    [qr, setQr] = useState(""),
    [needsEnrollment, setNeedsEnrollment] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const [register, setRegister] = useState(false),
    [confirmation, setConfirmation] = useState(""),
    [message, setMessage] = useState("");
  async function finish(token: string) {
    let result: { csrf_token: string };
    try {
      result = await api<{ csrf_token: string }>(
        presentation ? "/booth/session" : "/auth/session",
        {
          method: "POST",
          body: {},
          bearer: token,
        },
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === "identity_inactive") {
        clearSession();
        setFactor("");
        setQr("");
        setCode("");
        setNeedsEnrollment(false);
        setMessage(
          "Account verification is complete. Workspace access is awaiting administrator approval. Sign in again after your role has been assigned.",
        );
        return;
      }
      throw error;
    }
    rememberSession(result.csrf_token);
    const identity = await api<Identity>("/me");
    setPassword("");
    setCode("");
    onLogin(identity);
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!supabase || busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      if (register) {
        if (password !== confirmation)
          throw new Error("The passwords do not match.");
        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: window.location.origin },
        });
        if (error) throw error;
        setPassword("");
        setConfirmation("");
        setRegister(false);
        setMessage(
          "Check your email for the confirmation link, then return here and sign in. Creating an account does not grant access to transaction records.",
        );
      } else if (factor) {
        const { data, error } = await supabase.auth.mfa.challengeAndVerify({
          factorId: factor,
          code,
        });
        if (error) throw error;
        await finish(data.access_token);
      } else {
        const { data, error } = await supabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error)
          throw new Error(
            "Sign-in failed. Check your credentials and email verification.",
          );
        setPassword("");
        const factors = await supabase.auth.mfa.listFactors();
        if (factors.error) throw factors.error;
        const verified = factors.data.totp.find((f) => f.status === "verified");
        if (verified) setFactor(verified.id);
        else {
          setNeedsEnrollment(true);
        }
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function enroll() {
    if (!supabase) return;
    setBusy(true);
    setError(null);
    try {
      const { data, error } = await supabase.auth.mfa.enroll({
        factorType: "totp",
        friendlyName: "Rasid authenticator",
      });
      if (error) throw error;
      setQr(data.totp.qr_code);
      setFactor(data.id);
      setNeedsEnrollment(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <aside className="login-story">
        <Brand />
        <div className="story-copy">
          <div className="eyebrow">TRANSACTION INTELLIGENCE</div>
          <h1>
            See the signal.
            <br />
            <em>Make the right call.</em>
          </h1>
          <p>
            A connected workspace for investigating suspicious activity,
            verifying transactions, and making accountable decisions.
          </p>
          <div className="story-flow">
            <span>Detect</span>
            <i />
            <span>Investigate</span>
            <i />
            <span>Resolve</span>
          </div>
        </div>
        <div className="story-footer">
          <ShieldCheck size={18} />
          Senior project · Synthetic financial data only
        </div>
      </aside>
      <main className="login-main">
        <div className="login-form">
          <span className="login-icon">
            <Fingerprint size={30} />
          </span>
          <div className="eyebrow">SECURE WORKSPACE</div>
          <h2>
            {register
              ? "Create your project account"
              : factor
                ? "Verify it’s you"
                : needsEnrollment
                  ? "Protect your account"
                  : "Welcome back"}
          </h2>
          <p>
            {register
              ? "Choose your own password. We will send you an email confirmation."
              : factor
                ? "Enter the six-digit code from your authenticator."
                : presentation
                  ? "Sign in once with your authenticator, then switch between Bank feed, Analyst, Supervisor and Administrator."
                  : "Sign in to your transaction monitoring workspace."}
          </p>
          <ErrorBox error={error} />
          {message && (
            <div className="notice success" role="status">
              {message}
            </div>
          )}
          {!authConfigured && (
            <div className="notice error">
              Authentication is not configured. Add the project’s public
              configuration and rebuild the frontend.
            </div>
          )}
          <form onSubmit={submit}>
            <fieldset disabled={busy || !authConfigured}>
              {factor ? (
                <>
                  <Field label="Authenticator code">
                    <input
                      autoFocus
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      pattern="[0-9]{6}"
                      maxLength={6}
                      required
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                    />
                  </Field>
                  {qr && (
                    <div className="enrollment">
                      <img
                        src={qr}
                        alt="Scan this code with your authenticator"
                      />
                      <p>
                        Scan this code, then enter the generated verification
                        code.
                      </p>
                    </div>
                  )}
                </>
              ) : !needsEnrollment ? (
                <>
                  <Field label="Email address">
                    <input
                      type="email"
                      autoComplete="username"
                      placeholder="you@example.com"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </Field>
                  <Field label="Password">
                    <input
                      type="password"
                      autoComplete={
                        register ? "new-password" : "current-password"
                      }
                      minLength={register ? 12 : undefined}
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </Field>
                  {register && (
                    <Field label="Confirm password">
                      <input
                        type="password"
                        autoComplete="new-password"
                        required
                        minLength={12}
                        value={confirmation}
                        onChange={(e) => setConfirmation(e.target.value)}
                      />
                    </Field>
                  )}
                </>
              ) : (
                <p>
                  Use an authenticator app to add a second verification step.
                  Your administrator can grant workspace access after
                  enrollment.
                </p>
              )}
              {needsEnrollment ? (
                <div>
                  <button type="button" onClick={enroll}>
                    Set up authenticator
                  </button>
                  {!presentation && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={async () => {
                        setBusy(true);
                        setError(null);
                        try {
                          const { data } = await supabase!.auth.getSession();
                          if (data.session)
                            await finish(data.session.access_token);
                        } catch (e) {
                          setError(e);
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Continue as an authorized ingestion account
                    </button>
                  )}
                </div>
              ) : (
                <button className="primary wide" type="submit">
                  {busy
                    ? "Verifying…"
                    : register
                      ? "Create account and send confirmation"
                      : factor
                        ? "Verify and continue"
                        : "Continue securely"}
                  <ArrowRight size={18} />
                </button>
              )}
            </fieldset>
          </form>
          {!presentation && !factor && !needsEnrollment && (
            <button
              type="button"
              disabled={busy}
              className="text-button"
              onClick={() => {
                setRegister(!register);
                setError(null);
                setMessage("");
                setPassword("");
                setConfirmation("");
              }}
            >
              {register
                ? "Already have an account? Sign in"
                : "New to the project? Create an account"}
            </button>
          )}
          {factor && (
            <button
              className="text-button"
              onClick={() => {
                setFactor("");
                setCode("");
                setQr("");
                setError(null);
              }}
            >
              Use a different account
            </button>
          )}
          <div className="login-help">
            <LockKeyhole size={16} />
            <span>
              Access is limited to approved project accounts. Staff access
              requires multi-factor authentication.
            </span>
          </div>
          <small className="muted">
            Local application · Supabase authentication
          </small>
        </div>
      </main>
    </div>
  );
}
