import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { ErrorBox, Field } from "./ui";
type StaffRole = "analyst" | "supervisor" | "admin";
type User = {
  id: string;
  name: string;
  email: string;
  role: StaffRole;
  status: string;
  active: boolean | null;
  version: number;
  is_self: boolean;
};
export default function StaffUsers({ enabled }: { enabled: boolean }) {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState(""),
    [name, setName] = useState("");
  const [role, setRole] = useState<StaffRole>("analyst");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null),
    [message, setMessage] = useState("");
  async function refresh() {
    const result = await api<{ items: User[] }>("/staff/users");
    setUsers(result.items);
  }
  useEffect(() => {
    if (enabled) refresh().catch(setError);
  }, [enabled]);
  async function invite(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      const result = await api<{ status: string }>("/staff/users", {
        method: "POST",
        body: { name, email, role },
      });
      setMessage(
        result.status === "RESTORED_RESET_SENT"
          ? "User restored with the selected role. A fresh password setup link has been requested by email."
          : "Invitation sent. The user chooses their password and enrolls an authenticator.",
      );
      setEmail("");
      setName("");
    } catch (e) {
      setError(e);
    } finally {
      await refresh().catch(setError);
      setBusy(false);
    }
  }
  async function retry(user: User) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api(`/staff/users/${user.id}/invite`, { method: "POST" });
      setMessage("Invitation sent.");
    } catch (e) {
      setError(e);
    } finally {
      await refresh().catch(setError);
      setBusy(false);
    }
  }
  async function resetPassword(user: User) {
    if (
      busy ||
      !window.confirm(`Send a fresh password reset link to ${user.email}?`)
    )
      return;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api(`/staff/users/${user.id}/reset-password`, { method: "POST" });
      setMessage(
        "Password reset email requested. Ask the user to open the newest email.",
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function remove(user: User) {
    if (
      busy ||
      !window.confirm(
        `Delete ${user.name} (${user.email}) from this workspace? Their access will be revoked and their audit history retained.`,
      )
    )
      return;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api(`/staff/users/${user.id}`, {
        method: "DELETE",
        body: { expected_version: user.version },
      });
      await refresh();
      setMessage("User deleted from this workspace.");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function change(user: User, active: boolean, nextRole: StaffRole) {
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      await api(`/staff/users/${user.id}`, {
        method: "PATCH",
        body: { expected_version: user.version, active, role: nextRole },
      });
      await refresh();
      setMessage("Access updated. Previous sessions have been revoked.");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!enabled)
    return (
      <p>User invitations are available in the configured staff workspace.</p>
    );
  return (
    <section className="panel">
      <h1>Users</h1>
      <p>
        Invite staff to their assigned role. Passwords are set privately by each
        user.
      </p>
      <p>
        Your own account is protected here. Another administrator must manage
        your access.
      </p>
      <ErrorBox error={error} />
      {message && <p role="status">{message}</p>}
      <form onSubmit={invite}>
        <fieldset disabled={busy}>
          <Field label="Full name">
            <input
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
          <Field label="Work email">
            <input
              type="email"
              required
              maxLength={254}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field label="Role">
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as StaffRole)}
            >
              <option value="analyst">Analyst</option>
              <option value="supervisor">Supervisor</option>
              <option value="admin">Administrator</option>
            </select>
          </Field>
          <button type="submit">
            {busy ? "Sending…" : "Create user and send invitation"}
          </button>
        </fieldset>
      </form>
      <button
        className="secondary"
        disabled={busy}
        onClick={() => refresh().catch(setError)}
      >
        Refresh users
      </button>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Access</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                {user.name}
                {user.is_self && <small> (Your account)</small>}
              </td>
              <td>{user.email}</td>
              <td>
                <select
                  aria-label={`Role for ${user.email}`}
                  disabled={busy || user.is_self || user.active === null}
                  value={user.role}
                  onChange={(e) =>
                    change(
                      user,
                      user.active === true,
                      e.target.value as StaffRole,
                    )
                  }
                >
                  <option value="analyst">Analyst</option>
                  <option value="supervisor">Supervisor</option>
                  <option value="admin">Administrator</option>
                </select>
              </td>
              <td>
                {user.active === null
                  ? "Invitation pending — delivery/access not confirmed"
                  : user.active
                    ? "Enabled"
                    : "Disabled"}
              </td>
              <td>
                {user.active === null && (
                  <button
                    disabled={busy || user.is_self}
                    onClick={() => retry(user)}
                  >
                    Retry invitation
                  </button>
                )}
                {user.active !== null && (
                  <button
                    disabled={busy || user.is_self}
                    onClick={() => change(user, !user.active, user.role)}
                  >
                    {user.active ? "Disable" : "Enable"}
                  </button>
                )}
                {user.active === true && (
                  <button
                    disabled={busy || user.is_self}
                    onClick={() => resetPassword(user)}
                  >
                    Send password reset link
                  </button>
                )}
                <button
                  disabled={busy || user.is_self}
                  onClick={() => remove(user)}
                >
                  Delete user
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
