import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CaseWorkspace from "../src/CaseWorkspace";
import InvestigationGuide from "../src/InvestigationGuide";
import ScoringEvidence from "../src/ScoringEvidence";
import { analyst, caseId, fixture, response, summary } from "./fixtures";
afterEach(() => vi.unstubAllGlobals());
function mock() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      response(fixture(String(input).replace("/api/v1", ""))),
    ),
  );
}
it("uses freely accessible tabs and submits recommendations without step reviews", async () => {
  mock();
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  await screen.findByRole("heading", { name: "Investigation" });
  expect(
    screen.queryByRole("button", { name: "Record final decision" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Investigation progress"),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(
    await screen.findByRole("button", { name: "Submit to supervisor" }),
  ).toBeEnabled();
  await userEvent.type(
    screen.getByLabelText("Reason and supporting evidence"),
    "Reviewed the payment and customer history",
  );
  await userEvent.click(screen.getByRole("button", { name: "AI assessment" }));
  await screen.findByRole("heading", { name: "Assess the evidence" });
  await userEvent.click(screen.getByRole("button", { name: "Evidence" }));
  await screen.findByRole("heading", { name: "Scoring evidence" });
  await userEvent.click(screen.getByRole("button", { name: "Calls" }));
  await screen.findByRole("heading", { name: "Customer contact" });
  await userEvent.click(screen.getByRole("button", { name: "Actions" }));
  expect(screen.getByLabelText("Reason and supporting evidence")).toHaveValue(
    "Reviewed the payment and customer history",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Submit to supervisor" }),
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/cases/" + caseId + "/action-proposals",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_version: 4,
          action: "HOLD_TRANSFER",
          reason: "Reviewed the payment and customer history",
        }),
      }),
    ),
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([input]) =>
        String(input).includes("investigation-progress"),
      ),
  ).toBe(false);
});
it("saves a manual assessment with an explicit reason and advances the stage", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        overview: { reason: "Reviewed" },
        scoring: { reason: "Reviewed" },
      }),
    ),
  );
  const next = vi.fn();
  render(
    <InvestigationGuide
      id={caseId}
      version={4}
      revision={0}
      tab="AI assessment"
      onTab={next}
      onRefresh={() => {}}
      editable
      pending={false}
    />,
  );
  await screen.findByText("2 of 4 reviews recorded");
  await userEvent.selectOptions(
    screen.getByLabelText("Review approach"),
    "MANUAL_REVIEW",
  );
  await userEvent.type(
    screen.getByLabelText("Step review note"),
    "AI unavailable; reviewed account history manually.",
  );
  await userEvent.click(
    screen.getByRole("button", { name: /Save review and continue/ }),
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("investigation-progress"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_version: 4,
          step: "assessment",
          outcome: "MANUAL_REVIEW",
          reason: "AI unavailable; reviewed account history manually.",
        }),
      }),
    ),
  );
  expect(next).toHaveBeenCalledWith("Calls");
});
it("renders model contributions as readable evidence and handles missing scores", () => {
  render(
    <ScoringEvidence
      transaction={{
        ...summary.transaction,
        result: {
          risk_score: 0.87,
          severity: "CRITICAL",
          contributions: [
            {
              label: "Transaction amount",
              value: "SAR 18,450",
              contribution: 1.2,
            },
          ],
        },
      }}
    />,
  );
  expect(screen.getByText("Transaction amount")).toBeInTheDocument();
  expect(screen.getByText("SAR 18,450")).toBeInTheDocument();
  expect(screen.getByText(/Raises model output/)).toBeInTheDocument();
  expect(screen.getByText(/not a calibrated probability/)).toBeInTheDocument();
});
it("retains recorded progress in the read-only supervisor view", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({ overview: { reason: "Payment details reviewed" } }),
    ),
  );
  render(
    <InvestigationGuide
      id={caseId}
      version={4}
      revision={0}
      tab="Overview"
      onTab={() => {}}
      onRefresh={() => {}}
      editable={false}
      pending
    />,
  );
  expect(
    await screen.findByText("Recorded: Payment details reviewed"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Save review and continue/ }),
  ).not.toBeInTheDocument();
});
