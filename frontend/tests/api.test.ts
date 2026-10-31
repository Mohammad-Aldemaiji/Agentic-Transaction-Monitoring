import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  clearSession,
  hasSession,
  minorUnits,
  rememberSession,
} from "../src/api";
import { response } from "./fixtures";
beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());
describe("request boundary", () => {
  it("keeps the session credential in an HttpOnly cookie and sends CSRF for writes", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ id: "saved" }));
    vi.stubGlobal("fetch", fetch);
    rememberSession("csrf-only");
    await api("/cases/x/notes", {
      method: "POST",
      body: { expected_version: 4, text: "Evidence checked" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/cases/x/notes",
      expect.objectContaining({
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-only" }),
        body: JSON.stringify({ expected_version: 4, text: "Evidence checked" }),
      }),
    );
    expect(localStorage.length).toBe(0);
  });
  it("does not retry a stale mutation", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        response(
          { error: { code: "evidence_changed", request_id: "ref" } },
          409,
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(
      api("/cases/x/decisions", { method: "POST", body: {} }),
    ).rejects.toMatchObject({ status: 409, requestId: "ref" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("clears expired sessions and notifies the application", async () => {
    rememberSession("csrf");
    const handler = vi.fn();
    window.addEventListener("session-expired", handler, { once: true });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          response({ error: { code: "session_expired" } }, 401),
        ),
    );
    await expect(api("/me")).rejects.toMatchObject({ status: 401 });
    expect(hasSession()).toBe(false);
    expect(handler).toHaveBeenCalledOnce();
  });
  it("preserves a stable idempotency key supplied by the intake form", async () => {
    const fetch = vi.fn().mockResolvedValue(response({}));
    vi.stubGlobal("fetch", fetch);
    await api("/transactions", {
      method: "POST",
      body: {},
      idempotency: "stable-key",
    });
    expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
      "stable-key",
    );
  });
  it("rejects a cross-origin path before sending credentials", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(api("//evil.example")).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
});
describe("financial input", () => {
  it("converts decimals exactly without floating-point rounding", () => {
    expect(minorUnits("0.29", "SAR")).toBe(29);
    expect(minorUnits("123.456", "KWD")).toBe(123456);
  });
  it.each(["0", "-10", "1.001", "NaN", "1e3", "100000000000"])(
    "rejects invalid SAR amount %s",
    (value) => expect(() => minorUnits(value, "SAR")).toThrow(),
  );
});
