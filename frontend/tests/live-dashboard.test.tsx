import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import Queue, { LIVE_MS, ago } from "../src/Queue";
import { fixture, response } from "./fixtures";

let polls = 0;
function dashboard() {
  polls++;
  return {
    ...(fixture("/dashboard") as object),
    cases: {
      OPEN: 8 + polls,
      IN_REVIEW: 12,
      AWAITING_CUSTOMER: 3,
      RESOLVED: 27,
    },
    decided_today: 4,
    generated_at: "2026-11-06T12:00:00Z",
    transactions: {
      received: 1200 + polls * 100,
      received_today: 300,
      received_last_hour: 100,
      scored: 1190,
      pending: 10,
      failed: 0,
      flagged: 130,
      flagged_last_hour: 13,
      flag_rate: 0.1083,
      average_risk_score: 23.4,
      last_received_at: new Date().toISOString(),
    },
  };
}
beforeEach(() => {
  polls = 0;
  sessionStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).replace("/api/v1", "");
      return response(path === "/dashboard" ? dashboard() : fixture(path));
    }),
  );
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const calls = (prefix: string) =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([url]) => String(url).startsWith("/api/v1" + prefix))
    .length;

it("updates the number cards by itself without reloading the queue list", async () => {
  render(<Queue openCase={() => {}} />);
  expect(await screen.findByText("1,300")).toBeInTheDocument();
  expect(screen.getByText(/^Live, updated/)).toBeInTheDocument();
  expect(screen.getByText("Live transaction flow")).toBeInTheDocument();
  expect(
    screen.getByText("10.8% of received, 13 in the last hour"),
  ).toBeInTheDocument();
  expect(screen.getByText("23.40")).toBeInTheDocument();
  const kpi = (name: string) => within(screen.getByRole("region", { name }));
  // Numbers ease in, so wait for them to settle.
  expect(await kpi("Decided today").findByText("4")).toBeInTheDocument();
  expect(await kpi("Open cases").findByText("24")).toBeInTheDocument(); // 9 + 12 + 3 open
  const queueLoads = calls("/cases?");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_MS);
  });
  expect(await screen.findByText("1,400")).toBeInTheDocument();
  expect(await kpi("Open cases").findByText("25")).toBeInTheDocument();
  // A new case between live updates is announced, but the list is not reloaded.
  expect(screen.getByText("New case flagged")).toBeInTheDocument();
  expect(
    screen.getByRole("img", { name: /cases flagged in the last 14 days/ }),
  ).toBeInTheDocument();
  expect(screen.getByText("1,400").closest(".stat")).toHaveClass("changed");
  expect(calls("/dashboard")).toBe(2);
  expect(calls("/cases?")).toBe(queueLoads);
});

it("pauses while the tab is hidden", async () => {
  render(<Queue openCase={() => {}} />);
  await screen.findByText("1,300");
  const hidden = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("hidden");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_MS * 3);
  });
  expect(calls("/dashboard")).toBe(1);
  hidden.mockReturnValue("visible");
  await act(async () => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(await screen.findByText("1,400")).toBeInTheDocument();
});

it("hides team-wide traffic when the role only sees its own cases", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const path = String(input).replace("/api/v1", "");
    return response(
      path === "/dashboard"
        ? { ...dashboard(), transactions: null }
        : fixture(path),
    );
  });
  render(<Queue openCase={() => {}} />);
  await screen.findByText("SYN-ACCOUNT-1042");
  expect(screen.queryByText("Live transaction flow")).not.toBeInTheDocument();
  expect(screen.getByText("Open cases")).toBeInTheDocument();
});

it("describes ages in plain words", () => {
  const now = Date.parse("2026-11-06T12:00:00Z");
  expect(ago("2026-11-06T11:59:40Z", now)).toBe("just now");
  expect(ago("2026-11-06T11:15:00Z", now)).toBe("45 min ago");
  expect(ago("2026-11-06T07:00:00Z", now)).toBe("5 h ago");
  expect(ago("2026-11-01T12:00:00Z", now)).toBe("5 days ago");
  expect(ago(null, now)).toBeNull();
});
