import { useState } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
const mocks = vi.hoisted(() => ({
  resetPasswordForEmail: vi.fn(),
  signInWithPassword: vi.fn(),
  listFactors: vi.fn(),
  challengeAndVerify: vi.fn(),
  enroll: vi.fn(),
  getSession: vi.fn(),
  signOut: vi.fn(),
  api: vi.fn(),
  remember: vi.fn(),
}));
vi.mock("../src/auth", () => ({
  authConfigured: true,
  supabase: {
    auth: {
      resetPasswordForEmail: mocks.resetPasswordForEmail,
      signInWithPassword: mocks.signInWithPassword,
      getSession: mocks.getSession,
      signOut: mocks.signOut,
      mfa: {
        listFactors: mocks.listFactors,
        challengeAndVerify: mocks.challengeAndVerify,
        enroll: mocks.enroll,
      },
    },
  },
}));
vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  api: mocks.api,
  rememberSession: mocks.remember,
}));
// The camera scan is tested separately; here it hands back fixed samples.
vi.mock("../src/FaceScan", () => ({
  default: ({
    mode,
    onCapture,
  }: {
    mode: string;
    onCapture: (samples: number[][]) => Promise<void>;
  }) => {
    const [problem, setProblem] = useState("");
    return (
      <div>
        <button
          onClick={() =>
            onCapture([[0.1], [0.2], [0.3]]).catch((e: Error) =>
              setProblem(e.message),
            )
          }
        >
          scan {mode}
        </button>
        {problem && <p>{problem}</p>}
      </div>
    );
  },
}));
import Login from "../src/Login";
beforeEach(() => vi.resetAllMocks());
async function credentials() {
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "student@example.invalid",
  );
  await userEvent.type(
    screen.getByLabelText("Password", { exact: true }),
    "synthetic-test-password",
  );
}
it("offers recovery to a staff member without offering public registration", async () => {
  mocks.resetPasswordForEmail.mockResolvedValue({ error: null });
  render(<Login staffAccess onLogin={() => {}} />);
  expect(screen.queryByText(/Create an account/i)).not.toBeInTheDocument();
  await userEvent.type(
    screen.getByLabelText("Email address"),
    "staff@example.invalid",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Forgot password?" }),
  );
  await screen.findByText(/If this email has an account/);
  expect(mocks.resetPasswordForEmail).toHaveBeenCalledWith(
    "staff@example.invalid",
    {
      redirectTo: window.location.origin + "/?account=setup",
    },
  );
});
const SAMPLES = [[0.1], [0.2], [0.3]];
it("signs staff in with a password and then their face, with no authenticator step", async () => {
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "password-token" } },
    error: null,
  });
  mocks.api
    .mockResolvedValueOnce({ enrolled: true })
    .mockResolvedValueOnce({ csrf_token: "csrf-only" })
    .mockResolvedValueOnce({ id: "principal", role: "analyst", team: "booth" });
  const onLogin = vi.fn();
  render(<Login staffAccess presentation onLogin={onLogin} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await screen.findByRole("heading", { name: "Verify it’s you" });
  expect(screen.queryByLabelText("Authenticator code")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  expect(mocks.listFactors).not.toHaveBeenCalled();
  expect(mocks.enroll).not.toHaveBeenCalled();
  expect(mocks.api).toHaveBeenCalledTimes(1);
  expect(mocks.api).toHaveBeenCalledWith("/staff/face/status", {
    method: "POST",
    body: {},
    bearer: "password-token",
  });
  await userEvent.click(screen.getByRole("button", { name: "scan verify" }));
  await waitFor(() => expect(onLogin).toHaveBeenCalled());
  expect(mocks.api).toHaveBeenNthCalledWith(2, "/staff/session", {
    method: "POST",
    body: { samples: SAMPLES, enroll: false },
    bearer: "password-token",
  });
  expect(mocks.remember).toHaveBeenCalledWith("csrf-only");
  expect(mocks.challengeAndVerify).not.toHaveBeenCalled();
});
it("asks a new staff member to agree before enrolling their face", async () => {
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "password-token" } },
    error: null,
  });
  mocks.api
    .mockResolvedValueOnce({ enrolled: false })
    .mockResolvedValueOnce({ csrf_token: "csrf-only" })
    .mockResolvedValueOnce({ id: "principal", role: "admin", team: "booth" });
  const onLogin = vi.fn();
  render(<Login staffAccess onLogin={onLogin} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await screen.findByRole("heading", { name: "Set up face sign-in" });
  expect(
    screen.queryByRole("button", { name: /^scan/ }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: /I agree, start the camera/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: "scan enroll" }));
  await waitFor(() => expect(onLogin).toHaveBeenCalled());
  expect(mocks.api).toHaveBeenNthCalledWith(2, "/staff/session", {
    method: "POST",
    body: { samples: SAMPLES, enroll: true },
    bearer: "password-token",
  });
});
it("keeps a staff member on the face step when the face does not match", async () => {
  const { ApiError } = await import("../src/api");
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "password-token" } },
    error: null,
  });
  mocks.api
    .mockResolvedValueOnce({ enrolled: true })
    .mockRejectedValueOnce(new ApiError(401, "face_not_recognized"));
  const onLogin = vi.fn();
  render(<Login staffAccess onLogin={onLogin} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "scan verify" }),
  );
  await screen.findByText(/Your face did not match this account/);
  expect(onLogin).not.toHaveBeenCalled();
  expect(mocks.remember).not.toHaveBeenCalled();
  expect(
    screen.getByRole("heading", { name: "Verify it’s you" }),
  ).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Use a different account" }),
  );
  expect(await screen.findByLabelText("Email address")).toBeInTheDocument();
});
it("keeps password-verified but unprovisioned staff outside the workspace", async () => {
  const { ApiError } = await import("../src/api");
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "password-token" } },
    error: null,
  });
  mocks.api.mockRejectedValue(new ApiError(401, "identity_inactive"));
  render(<Login staffAccess onLogin={() => {}} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await screen.findByText(
    /Workspace access is awaiting administrator approval/,
  );
  expect(
    screen.queryByRole("button", { name: /^scan/ }),
  ).not.toBeInTheDocument();
  expect(mocks.remember).not.toHaveBeenCalled();
});
it("offers MFA enrollment before attempting workspace access for a new account", async () => {
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "unprivileged" } },
    error: null,
  });
  mocks.listFactors.mockResolvedValue({ data: { totp: [] }, error: null });
  render(<Login onLogin={() => {}} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await screen.findByRole("button", { name: "Set up authenticator" });
  expect(mocks.api).not.toHaveBeenCalled();
  expect(
    screen.queryByLabelText("Password", { exact: true }),
  ).not.toBeInTheDocument();
});

it("keeps verified but unprovisioned users outside the workspace", async () => {
  const { ApiError } = await import("../src/api");
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "pre-mfa" } },
    error: null,
  });
  mocks.listFactors.mockResolvedValue({
    data: { totp: [{ id: "factor-1", status: "verified" }] },
    error: null,
  });
  mocks.challengeAndVerify.mockResolvedValue({
    data: { access_token: "after-mfa" },
    error: null,
  });
  mocks.api.mockRejectedValue(new ApiError(401, "identity_inactive"));
  const onLogin = vi.fn();
  render(<Login onLogin={onLogin} />);
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await userEvent.type(
    await screen.findByLabelText("Authenticator code"),
    "123456",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and continue" }),
  );
  await screen.findByText(
    /Workspace access is awaiting administrator approval/,
  );
  expect(onLogin).not.toHaveBeenCalled();
  expect(mocks.remember).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Authenticator code")).not.toBeInTheDocument();
});

it("requires MFA before opening presentation roles through the protected session endpoint", async () => {
  mocks.signInWithPassword.mockResolvedValue({
    data: { session: { access_token: "pre-mfa" } },
    error: null,
  });
  mocks.listFactors.mockResolvedValue({
    data: { totp: [{ id: "factor-1", status: "verified" }] },
    error: null,
  });
  mocks.challengeAndVerify.mockResolvedValue({
    data: { access_token: "after-mfa" },
    error: null,
  });
  mocks.api
    .mockResolvedValueOnce({ csrf_token: "csrf-only" })
    .mockResolvedValueOnce({ id: "analyst", role: "analyst", team: "booth" });
  const onLogin = vi.fn();
  render(<Login presentation onLogin={onLogin} />);
  expect(
    screen.queryByText("New to the project? Create an account"),
  ).not.toBeInTheDocument();
  await credentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Continue securely" }),
  );
  await screen.findByLabelText("Authenticator code");
  expect(mocks.api).not.toHaveBeenCalled();
  await userEvent.type(screen.getByLabelText("Authenticator code"), "123456");
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and continue" }),
  );
  await waitFor(() => expect(onLogin).toHaveBeenCalled());
  expect(mocks.api).toHaveBeenNthCalledWith(1, "/booth/session", {
    method: "POST",
    body: {},
    bearer: "after-mfa",
  });
});
