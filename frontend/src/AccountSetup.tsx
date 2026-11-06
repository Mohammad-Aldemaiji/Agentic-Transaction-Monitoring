import { useState, type FormEvent } from "react";
import { supabase } from "./auth";
import { Brand, ErrorBox, Field } from "./ui";
const fragment = new URLSearchParams(window.location.hash.slice(1));
const query = new URLSearchParams(window.location.search);
const kind = fragment.get("type") ?? query.get("type");
let callback =
  kind === "invite" || kind === "recovery"
    ? {
        access: fragment.get("access_token"),
        refresh: fragment.get("refresh_token"),
        hash: query.get("token_hash"),
        type: kind as "invite" | "recovery",
      }
    : null;
const setupRequested = query.get("account") === "setup" || callback !== null;
if (callback || setupRequested)
  window.history.replaceState(null, "", window.location.pathname);
export const hasAccountSetup = () => setupRequested;
export default function AccountSetup({
  onDone,
  staffAccess = false,
}: {
  onDone: () => void;
  staffAccess?: boolean;
}) {
  const [ready, setReady] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const [password, setPassword] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [factor, setFactor] = useState(""),
    [code, setCode] = useState("");
  async function verify() {
    if (!supabase || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (!callback)
        throw new Error(
          "This link is missing, expired or already used. Request a new invitation or password reset.",
        );
      const result = callback.hash
        ? await supabase.auth.verifyOtp({
            token_hash: callback.hash,
            type: callback.type,
          })
        : callback.access && callback.refresh
          ? await supabase.auth.setSession({
              access_token: callback.access,
              refresh_token: callback.refresh,
            })
          : null;
      callback = null;
      if (!result || result.error || !result.data.session)
        throw new Error("This link could not be verified. Request a new link.");
      const factors = await supabase.auth.mfa.listFactors();
      if (factors.error) throw factors.error;
      setFactor(
        factors.data.totp.find((f) => f.status === "verified")?.id ?? "",
      );
      setReady(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!supabase || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (password !== confirmation)
        throw new Error("The passwords do not match.");
      if (factor) {
        const checked = await supabase.auth.mfa.challengeAndVerify({
          factorId: factor,
          code,
        });
        if (checked.error) throw checked.error;
      }
      const { error } = await supabase.auth.updateUser({ password });
      if (error) throw error;
      setPassword("");
      setConfirmation("");
      await supabase.auth.signOut({ scope: "local" });
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-form">
      <Brand />
      <h1>Set your password</h1>
      <p>
        {staffAccess
          ? "Choose your own password, then sign in and verify with your face."
          : "Choose your own password, then sign in and complete authenticator verification."}
      </p>
      <ErrorBox error={error} />
      {!ready ? (
        <button disabled={busy} onClick={verify}>
          Verify invitation or reset link
        </button>
      ) : (
        <form onSubmit={save}>
          <fieldset disabled={busy}>
            <Field label="New password">
              <input
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Field label="Confirm password">
              <input
                type="password"
                autoComplete="new-password"
                minLength={12}
                required
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
              />
            </Field>
            {factor && staffAccess && (
              <p className="muted">
                This account still has an authenticator app registered from
                before face sign-in, so Supabase asks for its code to change the
                password. The operator can remove old authenticators with the
                remove-authenticators command.
              </p>
            )}
            {factor && (
              <Field label="Authenticator code">
                <input
                  required
                  pattern="[0-9]{6}"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </Field>
            )}
            <button>Save password and return to sign-in</button>
          </fieldset>
        </form>
      )}
      <button className="text-button" disabled={busy} onClick={onDone}>
        Back to sign-in
      </button>
    </main>
  );
}
