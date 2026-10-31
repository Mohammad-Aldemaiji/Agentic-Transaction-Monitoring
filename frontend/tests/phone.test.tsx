import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PhoneVerification from "../src/PhoneVerification";
import AIResult from "../src/AIResult";
import { response } from "./fixtures";
import type { Event } from "../src/types";
afterEach(() => vi.unstubAllGlobals());
for (const outcome of ["confirmed", "denied", "unavailable"] as const) {
  it(`records ${outcome} through the versioned phone workflow`, async () => {
    let state = "RINGING",
      version = 1;
    const replies: string[] = [];
    vi.stubGlobal(
      "AudioContext",
      class {
        resume = vi.fn();
        close = vi.fn();
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, options?: RequestInit) => {
        if (input.endsWith("customer-reply")) {
          const body = JSON.parse(options!.body as string);
          expect(body.expected_version).toBe(version);
          replies.push(body.reply);
          version++;
          state =
            body.reply === "answer"
              ? "ACTIVE"
              : body.reply === "unavailable"
                ? "DECLINED"
                : "ENDED";
          return response({ state, version });
        }
        if (input.endsWith("/turns") || input.endsWith("/audio"))
          return response({ items: [] });
        return response({ id: "call", state, version });
      }),
    );
    render(<PhoneVerification callId="call" />);
    if (outcome === "unavailable") {
      await userEvent.click(
        await screen.findByRole("button", { name: "Unavailable" }),
      );
      await screen.findByText("Customer unavailable");
      expect(replies).toEqual(["unavailable"]);
    } else {
      await userEvent.click(
        await screen.findByRole("button", { name: "Accept" }),
      );
      await userEvent.click(
        await screen.findByRole("button", {
          name:
            outcome === "confirmed"
              ? "1 — Confirm payment"
              : "2 — Deny payment",
        }),
      );
      await screen.findByText("Call ended");
      expect(replies).toEqual(["answer", outcome]);
    }
  });
}
it("shows the returned AI explanation and full response", () => {
  const finding = {
    details: {
      model: "qwen3:4b",
      summary: "New beneficiary and unusual amount",
      analysis: {
        recommendation_reason: "Verify with the account holder",
        evidence_for_fraud: ["Unusual amount"],
        evidence_against_fraud: ["Known device"],
      },
    },
  } as unknown as Event;
  render(<AIResult finding={finding} />);
  expect(
    screen.getByText("New beneficiary and unusual amount"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Verify with the account holder"),
  ).toBeInTheDocument();
  expect(screen.getByText("Known device")).toBeInTheDocument();
  expect(screen.getByText("Full AI response · JSON")).toBeInTheDocument();
});
