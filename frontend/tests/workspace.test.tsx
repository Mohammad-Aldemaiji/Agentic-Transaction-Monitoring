import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Queue from "../src/Queue";
import CaseWorkspace from "../src/CaseWorkspace";
import CallPanel from "../src/CallPanel";
import { allowedPages } from "../src/App";
import { analyst, caseId, fixture, response, summary } from "./fixtures";
beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      response(fixture(String(input).replace("/api/v1", ""))),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());
it("shows real response values and opens the chosen case", async () => {
  const open = vi.fn();
  render(<Queue openCase={open} />);
  await screen.findByText("SYN-ACCOUNT-1042");
  expect(screen.getByText("87.00")).toBeInTheDocument();
  await userEvent.click(screen.getByText("CASE-AAAAAAAA"));
  expect(open).toHaveBeenCalledWith(caseId);
});
it("sends filters to the server, not just the currently displayed page", async () => {
  render(<Queue openCase={() => {}} />);
  await screen.findByText("SYN-ACCOUNT-1042");
  await userEvent.selectOptions(screen.getByLabelText("Severity"), "HIGH");
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("severity=HIGH"),
      expect.anything(),
    ),
  );
});
it("never exposes customer or administrative navigation to an analyst", () => {
  expect(allowedPages("analyst").map((p) => p.id)).toEqual(["queue"]);
  expect(allowedPages("participant").map((p) => p.id)).toEqual(["calls"]);
  expect(allowedPages("admin").some((p) => p.id === "queue")).toBe(false);
});
it("does not offer decision writes to an auditor", async () => {
  render(
    <CaseWorkspace
      id={caseId}
      identity={{ ...analyst, role: "auditor" }}
      onBack={() => {}}
    />,
  );
  await screen.findByText("Investigation");
  expect(
    screen.queryByRole("button", { name: "Record final decision" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Cancel investigation" }),
  ).not.toBeInTheDocument();
});
it("requires confirmation and sends the reviewed case version in a decision", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) =>
    response(fixture(String(input).replace("/api/v1", ""))),
  );
  vi.stubGlobal("fetch", fetch);
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  const reason = await screen.findByLabelText("Decision rationale");
  await userEvent.type(reason, "Reviewed the synthetic evidence");
  await userEvent.click(screen.getByLabelText(/I have reviewed/));
  await userEvent.click(
    screen.getByRole("button", { name: "Record final decision" }),
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/cases/" + caseId + "/decisions",
      expect.objectContaining({
        body: JSON.stringify({
          expected_version: 4,
          disposition: "INCONCLUSIVE",
          reason: "Reviewed the synthetic evidence",
        }),
      }),
    ),
  );
});
it("blocks closure while a customer call remains active", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      response(
        String(input).endsWith("/summary")
          ? { ...summary, active_call: { id: "call", state: "ACTIVE" } }
          : fixture(String(input).replace("/api/v1", "")),
      ),
    ),
  );
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  expect(
    await screen.findByRole("button", { name: "Record final decision" }),
  ).toBeDisabled();
});
it("renders note text safely rather than executing embedded markup", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      response(
        String(input).includes("/notes?")
          ? {
              items: [
                {
                  id: "n",
                  text: "<img src=x onerror=alert(1)>",
                  author_id: analyst.id,
                  created_at: "2026-10-25T00:00:00Z",
                },
              ],
            }
          : fixture(String(input).replace("/api/v1", "")),
      ),
    ),
  );
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  await screen.findByText("Investigation");
  await userEvent.click(
    screen.getByRole("button", { name: "Notes & findings" }),
  );
  await screen.findByText("<img src=x onerror=alert(1)>");
  expect(document.querySelector("img")).toBeNull();
});
it("sends customer answers with the current call version", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) =>
    response(
      String(input).endsWith("/calls/call-1")
        ? {
            id: "call-1",
            state: "ACTIVE",
            language: "ar",
            version: 3,
            expires_at: "2026-10-25T12:00:00Z",
          }
        : { items: [] },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <CallPanel
      callId="call-1"
      identity={{ ...analyst, role: "participant" }}
    />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "No, I did not" }),
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/calls/call-1/verification",
      expect.objectContaining({
        body: JSON.stringify({
          expected_version: 3,
          response: "NOT_AUTHORIZED",
        }),
      }),
    ),
  );
  expect(document.querySelector('[dir="rtl"]')).toBeInTheDocument();
});

it("requests a version-bound AI recommendation without proposing or approving an action", async () => {
  const request = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace("/api/v1", "");
    return response(
      path.endsWith("/summary")
        ? { ...summary, local_agent_enabled: true }
        : fixture(path),
    );
  });
  vi.stubGlobal("fetch", request);
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Analyse with local AI" }),
  );
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      "/api/v1/cases/" + caseId + "/agent-analysis",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_version: 4 }),
        signal: expect.any(AbortSignal),
      }),
    ),
  );
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([, options]) => options?.method === "POST");
  expect(writes).toHaveLength(1);
});
