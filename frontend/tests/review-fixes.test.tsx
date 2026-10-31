import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PhoneVerification from "../src/PhoneVerification";
import { response } from "./fixtures";
afterEach(() => vi.unstubAllGlobals());
it("retries a transient audio download failure", async () => {
  let downloads = 0;
  vi.stubGlobal(
    "AudioContext",
    class {
      resume = vi.fn(async () => {});
      close = vi.fn();
      decodeAudioData = vi.fn(async () => ({}));
      destination = {};
      createBufferSource = () => ({
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      });
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (input.endsWith("/audio/clip")) {
        downloads++;
        return downloads === 1
          ? response({}, 503)
          : new Response(new ArrayBuffer(8));
      }
      if (input.endsWith("/audio"))
        return response({ items: [{ id: "clip" }] });
      if (input.endsWith("/turns")) return response({ items: [] });
      return response({ id: "call", state: "ACTIVE", version: 2 });
    }),
  );
  render(<PhoneVerification callId="call" />);
  await screen.findByText(/Voice instructions could not be loaded/);
  await userEvent.click(screen.getByRole("button", { name: "Retry audio" }));
  await screen.findByRole("button", { name: "Play instructions" });
  expect(downloads).toBe(2);
  expect(
    screen.queryByText(/Voice instructions could not be loaded/),
  ).not.toBeInTheDocument();
});
it("notifies the parent when the server times out a call", async () => {
  let state = "ACTIVE";
  const onChange = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (input.endsWith("/audio") || input.endsWith("/turns"))
        return response({ items: [] });
      return response({ id: "call", state, version: 2 });
    }),
  );
  render(<PhoneVerification callId="call" onChange={onChange} />);
  await screen.findByText("Connected");
  state = "TIMED_OUT";
  await waitFor(
    () =>
      expect(
        screen.getByText("Customer unavailable / timed out"),
      ).toBeInTheDocument(),
    { timeout: 4000 },
  );
  expect(onChange).toHaveBeenCalledTimes(1);
});
import CaseWorkspace from "../src/CaseWorkspace";
import { analyst, caseId, fixture, summary } from "./fixtures";
it("shows newer AI findings after refresh and marks changed evidence", async () => {
  const finding = (id: number, text: string) => ({
    id,
    created_at: `2026-10-28T12:0${id}:00Z`,
    details: {
      source: "local_llm",
      summary: text,
      evidence_version: id + 4,
      analysis: { summary: text },
    },
  });
  const old = finding(1, "Older AI assessment"),
    fresh = finding(2, "Newer AI assessment");
  let version = 4;
  let findings: ReturnType<typeof finding>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const path = input.replace("/api/v1", "");
      if (path.endsWith("/agent-analysis")) {
        version = 5;
        findings = [old];
        return response(old);
      }
      if (path.endsWith("/summary"))
        return response({
          ...summary,
          case: { ...summary.case, version },
          local_agent_enabled: true,
        });
      if (path.endsWith("/findings?latest_ai=true"))
        return response({ items: findings });
      return response(fixture(path));
    }),
  );
  render(<CaseWorkspace id={caseId} identity={analyst} onBack={() => {}} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Analyse with local AI" }),
  );
  await screen.findByText("Older AI assessment");
  version = 6;
  findings = [old, fresh];
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("Newer AI assessment");
  expect(screen.queryByText("Older AI assessment")).not.toBeInTheDocument();
  version = 7;
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText(/Evidence has changed since this assessment/);
});

it("keeps playback available when a refreshed call needs a browser audio gesture", async () => {
  const resume = vi.fn(async () => {}),
    start = vi.fn();
  vi.stubGlobal(
    "AudioContext",
    class {
      state = "suspended";
      resume = resume;
      close = vi.fn();
      decodeAudioData = vi.fn(async () => ({}));
      destination = {};
      createBufferSource = () => ({ connect: vi.fn(), start, stop: vi.fn() });
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (input.endsWith("/audio/clip"))
        return new Response(new ArrayBuffer(8));
      if (input.endsWith("/audio"))
        return response({ items: [{ id: "clip" }], status: "READY" });
      if (input.endsWith("/turns")) return response({ items: [] });
      return response({ id: "call", state: "ACTIVE", version: 2 });
    }),
  );
  render(<PhoneVerification callId="call" />);
  const button = await screen.findByRole("button", {
    name: "Play instructions",
  });
  expect(button).toBeEnabled();
  expect(resume).not.toHaveBeenCalled();
  await userEvent.click(button);
  expect(start).toHaveBeenCalledTimes(1);
});

it("shows failed voice generation without blocking customer responses", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (input.endsWith("/audio"))
        return response({ items: [], status: "FAILED" });
      if (input.endsWith("/turns"))
        return response({
          items: [{ id: "turn", speaker: "bank", text: "Press 1 to confirm" }],
        });
      return response({ id: "call", state: "ACTIVE", version: 2 });
    }),
  );
  render(<PhoneVerification callId="call" />);
  await screen.findByRole("button", {
    name: "Voice unavailable — use transcript",
  });
  expect(screen.getByText("Press 1 to confirm")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "2 — Deny payment" }),
  ).toBeEnabled();
});
