import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Fingerprint,
  LockKeyhole,
  ScanFace,
  ShieldCheck,
} from "lucide-react";
import { supabase, authConfigured } from "./auth";
import { api, ApiError, clearSession, rememberSession } from "./api";
import { Brand, ErrorBox, Field } from "./ui";
import FaceScan from "./FaceScan";
import type { Identity } from "./types";
type FaceStep = { token: string; enrolled: boolean };
const AWAITING_APPROVAL =
  "Account verification is complete. Workspace access is awaiting administrator approval. Sign in again after your role has been assigned.";
export default function Login({
  onLogin,
  presentation = false,
  staffAccess = false,
}: {
  onLogin: (identity: Identity) => void;
  presentation?: boolean;
  staffAccess?: boolean;
}) {
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [code, setCode] = useState("");
  const [factor, setFactor] = useState(""),
    [qr, setQr] = useState(""),
    [needsEnrollment, setNeedsEnrollment] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  // Staff workspace: the second step is face verification (it replaced the authenticator code).
  const [face, setFace] = useState<FaceStep | null>(null),
    [consent, setConsent] = useState(false);
  function restart(notice = "") {
    setFace(null);
    setConsent(false);
    setPassword("");
    setMessage(notice);
    void supabase?.auth.signOut({ scope: "local" });
  }
  async function verifyFace(samples: number[][]) {
    if (!face) return;
    let result: { csrf_token: string };
    try {
      result = await api<{ csrf_token: string }>("/staff/session", {
        method: "POST",
        body: { samples, enroll: !face.enrolled },
        bearer: face.token,
      });
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "identity_inactive") {
          clearSession();
          restart(AWAITING_APPROVAL);
          return;
        }
        // An administrator reset or completed the enrollment in the meantime.
        if (error.code === "face_enrollment_required")
          setFace({ ...face, enrolled: false });
        if (error.code === "face_already_enrolled")
          setFace({ ...face, enrolled: true });
        if (
          error.status === 401 &&
          !["face_not_recognized", "identity_inactive"].includes(error.code)
        ) {
          restart("Your sign-in expired. Enter your password again.");
          return;
        }
      }
      throw error;
    }
    rememberSession(result.csrf_token);
    const identity = await api<Identity>("/me");
    setPassword("");
    setFace(null);
    onLogin(identity);
  }
  async function finish(token: string) {
    let result: { csrf_token: string };
    try {
      result = await api<{ csrf_token: string }>(
        staffAccess
          ? "/staff/session"
          : presentation
            ? "/booth/session"
            : "/auth/session",
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
        setMessage(AWAITING_APPROVAL);
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
      if (factor) {
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
        if (error || !data.session)
          throw new Error(
            "Sign-in failed. Check your credentials and email verification.",
          );
        setPassword("");
        if (staffAccess) {
          const token = data.session.access_token;
          try {
            const status = await api<{ enrolled: boolean }>(
              "/staff/face/status",
              { method: "POST", body: {}, bearer: token },
            );
            setFace({ token, enrolled: status.enrolled });
          } catch (e) {
            if (e instanceof ApiError && e.code === "identity_inactive") {
              restart(AWAITING_APPROVAL);
              return;
            }
            void supabase.auth.signOut({ scope: "local" });
            throw e;
          }
          return;
        }
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
            {face ? <ScanFace size={30} /> : <Fingerprint size={30} />}
          </span>
          <div className="eyebrow">
            {face ? "STEP 2 OF 2 · FACE VERIFICATION" : "SECURE WORKSPACE"}
          </div>
          <h2>
            {face
              ? face.enrolled
                ? "Verify it’s you"
                : "Set up face sign-in"
              : factor
                ? "Verify it’s you"
                : needsEnrollment
                  ? "Protect your account"
                  : "Welcome back"}
          </h2>
          <p>
            {face
              ? face.enrolled
                ? "Look at the camera to confirm your identity."
                : "Your face will be your second sign-in step. Enroll it once on this device’s camera."
              : factor
                ? "Enter the six-digit code from your authenticator."
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
          {face ? (
            <>
              {face.enrolled || consent ? (
                <FaceScan
                  key={face.enrolled ? "verify" : "enroll"}
                  mode={face.enrolled ? "verify" : "enroll"}
                  onCapture={verifyFace}
                />
              ) : (
                <div className="face-consent">
                  <p>
                    Your camera takes a few still measurements of your face.
                    Only these numbers are saved, in this workspace’s local
                    database, never a photo. An administrator can remove them at
                    any time, and they are deleted if your account is deleted.
                  </p>
                  <button
                    type="button"
                    className="primary wide"
                    onClick={() => setConsent(true)}
                  >
                    I agree, start the camera
                    <ArrowRight size={18} />
                  </button>
                </div>
              )}
              <button
                type="button"
                className="text-button"
                onClick={() => restart()}
              >
                Use a different account
              </button>
            </>
          ) : (
            <>
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
                            Scan this code, then enter the generated
                            verification code.
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
                          autoComplete="current-password"

                          required
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                        />
                      </Field>
                    </>
                  ) : (
                    <p>
                      Use an authenticator app to add a second verification
                      step. Your administrator can grant workspace access after
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
                              const { data } =
                                await supabase!.auth.getSession();
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
                        : factor
                          ? "Verify and continue"
                          : "Continue securely"}
                      <ArrowRight size={18} />
                    </button>
                  )}
                </fieldset>
              </form>
              {!factor && !needsEnrollment && (
                <button
                  type="button"
                  className="text-button"
                  disabled={busy || !email}
                  onClick={async () => {
                    if (!supabase) return;
                    setBusy(true);
                    setError(null);
                    try {
                      const result = await supabase.auth.resetPasswordForEmail(
                        email,
                        {
                          redirectTo:
                            window.location.origin + "/?account=setup",
                        },
                      );
                      if (result.error)
                        throw new Error(
                          "Password reset could not be requested. Please try again later.",
                        );
                      setMessage(
                        "If this email has an account, you will receive a password reset link.",
                      );
                    } catch (e) {
                      setError(e);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Forgot password?
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
            </>
          )}
          <div className="login-help">
            <LockKeyhole size={16} />
            <span>
              {staffAccess
                ? "Access is limited to approved project accounts. Staff sign in with a password and face verification."
                : "Access is limited to approved project accounts. Staff access requires multi-factor authentication."}
            </span>
          </div>
          <small className="muted">
            {staffAccess
              ? "Local application · Supabase password · On-device face matching"
              : "Local application · Supabase authentication"}
          </small>
        </div>
      </main>
    </div>
  );
}
