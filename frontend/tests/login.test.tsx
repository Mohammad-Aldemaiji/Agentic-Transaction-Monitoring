import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
const mocks = vi.hoisted(() => ({
  signUp: vi.fn(),
  signInWithPassword: vi.fn(),
  listFactors: vi.fn(),
  challengeAndVerify: vi.fn(),
  enroll: vi.fn(),
  getSession: vi.fn(),
  api: vi.fn(),
  remember: vi.fn(),
}));
vi.mock("../src/auth", () => ({
  authConfigured: true,
  supabase: {
    auth: {
      signUp: mocks.signUp,
      signInWithPassword: mocks.signInWithPassword,
      getSession: mocks.getSession,
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
it("account creation sends only user-entered credentials and never assigns roles", async () => {
  mocks.signUp.mockResolvedValue({ error: null });
  const onLogin = vi.fn();
  render(<Login onLogin={onLogin} />);
  await userEvent.click(
    screen.getByText("New to the project? Create an account"),
  );
  await credentials();
  await userEvent.type(
    screen.getByLabelText("Confirm password"),
    "synthetic-test-password",
  );
  await userEvent.click(
    screen.getByRole("button", {
      name: "Create account and send confirmation",
    }),
  );
  await screen.findByText(/Check your email for the confirmation link/);
  expect(mocks.signUp).toHaveBeenCalledWith({
    email: "student@example.invalid",
    password: "synthetic-test-password",
    options: { emailRedirectTo: window.location.origin },
  });
  expect(mocks.api).not.toHaveBeenCalled();
  expect(onLogin).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Password", { exact: true })).toHaveValue("");
});
it("rejects mismatched new passwords before contacting the provider", async () => {
  render(<Login onLogin={() => {}} />);
  await userEvent.click(
    screen.getByText("New to the project? Create an account"),
  );
  await credentials();
  await userEvent.type(
    screen.getByLabelText("Confirm password"),
    "different-test-password",
  );
  await userEvent.click(
    screen.getByRole("button", {
      name: "Create account and send confirmation",
    }),
  );
  await screen.findByText("The passwords do not match.");
  expect(mocks.signUp).not.toHaveBeenCalled();
});
it("exchanges only the MFA-verified access token for a backend cookie session", async () => {
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
    .mockResolvedValueOnce({ id: "principal", role: "analyst", team: "demo" });
  const onLogin = vi.fn();
  render(<Login onLogin={onLogin} />);
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
  expect(mocks.api).toHaveBeenNthCalledWith(1, "/auth/session", {
    method: "POST",
    body: {},
    bearer: "after-mfa",
  });
  expect(mocks.remember).toHaveBeenCalledWith("csrf-only");
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
