import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../src/App";
import { fixture, response } from "./fixtures";

vi.mock("../src/Login", () => ({
  default: ({ onLogin }: { onLogin: (identity: object) => void }) => (
    <button
      onClick={() => onLogin({ id: "analyst", role: "analyst", team: "booth" })}
    >
      Complete verified sign-in
    </button>
  ),
}));
afterEach(() => vi.unstubAllGlobals());
it("limits an analyst to their assigned workspace after sign-in", async () => {
  sessionStorage.clear();
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options: RequestInit) => {
      const path = input.replace("/api/v1", "");
      if (path === "/booth/config")
        return response({ enabled: true, staff_access: true });
      if (path === "/booth/role") {
        const role = JSON.parse(options.body as string).role;
        requests.push(role);
        return response({
          csrf_token: "booth-csrf",
          identity: { id: role, role, team: "booth", booth_mode: true },
        });
      }
      if (path === "/booth/customers" || path.startsWith("/transactions"))
        return response({ items: [] });
      return response(fixture(path));
    }),
  );
  render(<App />);
  await screen.findByRole("button", { name: "Complete verified sign-in" });
  expect(
    screen.queryByRole("button", { name: "Bank feed" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Complete verified sign-in" }),
  );
  await screen.findByText("Every signal, in focus.");
  for (const name of [
    "Bank feed",
    "Supervisor",
    "Administrator",
    "Users",
    "Switch role",
  ])
    expect(
      screen.queryByRole("button", { name, exact: true }),
    ).not.toBeInTheDocument();
  expect(requests).toEqual([]);
});
