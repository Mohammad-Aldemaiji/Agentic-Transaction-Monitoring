import { afterEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Transactions from "../src/Transactions";
import samples from "../src/sampleTraffic.json";
import { response } from "./fixtures";

afterEach(() => vi.unstubAllGlobals());
it("retries the same sample batch after failure and advances only on acceptance", async () => {
  const sent: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      if (url.endsWith("/transactions/batch")) {
        sent.push(JSON.parse(options.body as string));
        return sent.length === 1
          ? response({ error: { code: "storage_unavailable" } }, 503)
          : response({ items: [] }, 202);
      }
      return response({ items: [], next_cursor: null });
    }),
  );
  render(<Transactions />);
  const button = screen.getByRole("button", {
    name: "Simulate incoming traffic",
  });
  await userEvent.click(button);
  await screen.findByRole("alert");
  await userEvent.click(button);
  await screen.findByText(/10 \/ 50 samples replayed/);
  expect(sent[0]).toEqual({ items: samples.slice(0, 10) });
  expect(sent[1]).toEqual(sent[0]);
  await userEvent.click(button);
  await screen.findByText(/20 \/ 50 samples replayed/);
  expect(sent[2]).toEqual({ items: samples.slice(10, 20) });
});

it("makes the workspace sample button submit only a bounded offset", async () => {
  const SampleTraffic = (await import("../src/SampleTraffic")).default;
  const fetch = vi.fn(async () => response({ accepted: 10 }, 202));
  vi.stubGlobal("fetch", fetch);
  render(<SampleTraffic />);
  await userEvent.click(
    screen.getByRole("button", { name: "Simulate incoming traffic" }),
  );
  await screen.findByText(/10 \/ 50 samples submitted/);
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/sample-traffic",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ offset: 0 }),
    }),
  );
});
