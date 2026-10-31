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
it("offers four booth roles, switches authoritative identity and omits a customer role tab", async () => {
  sessionStorage.clear();
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options: RequestInit) => {
      const path = input.replace("/api/v1", "");
      if (path === "/booth/config") return response({ enabled: true });
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
  await userEvent.click(
    await screen.findByRole("button", { name: "Bank feed", exact: true }),
  );
  await screen.findByRole("button", { name: /Simulate incoming traffic/ });
  expect(
    screen.queryByRole("button", { name: "Customer", exact: true }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "Analyst", exact: true }),
  );
  await screen.findByText("Every signal, in focus.");
  expect(requests).toEqual(["ingestion", "analyst"]);
  expect(
    screen.getByRole("button", { name: "Analyst", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
});
