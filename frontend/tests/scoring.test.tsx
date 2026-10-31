import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ScoringDemo from "../src/ScoringDemo";
import { response } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());
it("distinguishes historical context from evaluated payments and preserves failure counts", async () => {
  const metric = {
    transactions: 10,
    fraud: 2,
    caught: 1,
    missed: 1,
    false_alarms: 1,
    recall: 0.5,
    false_positive_rate: 0.125,
    threshold: 0.1,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        scope: "Sample study only",
        test_period: "August",
        validation_period: "July",
        test: { baseline: metric, candidate: metric },
        limitations: ["Not population performance"],
        customers: [
          {
            name: "Sample one",
            account_id: "SYN-1",
            scenario: "Mixed",
            transactions: 2,
            fraud: 1,
            evaluated: 1,
            history: [
              {
                id: "history-only",
                timestamp: "2026-01-01T00:00:00Z",
                amount: 10,
                currency: "SAR",
                channel: "CARD",
                authorization: "APPROVED",
                probability: null,
                fraud_label: false,
                baseline_alert: null,
                candidate_alert: null,
              },
              {
                id: "evaluated-payment",
                timestamp: "2026-08-20T00:00:00Z",
                amount: 100,
                currency: "SAR",
                channel: "CARD",
                authorization: "APPROVED",
                probability: 0.2,
                fraud_label: true,
                baseline_alert: true,
                candidate_alert: true,
              },
            ],
          },
        ],
      }),
    ),
  );
  render(<ScoringDemo />);
  await screen.findByText("Not evaluated");
  expect(screen.getByText("Fraud missed")).toBeInTheDocument();
  expect(screen.getByText(/does not yet meet/)).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText("Show evaluated payments only"));
  expect(
    screen.queryByRole("cell", { name: /history-only/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("cell", { name: /evaluated-payment/ }),
  ).toBeInTheDocument();
});
