import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, detailText, OFFLINE, setCsrf, statusText } from "./api";
import { changes } from "./pages/People";
import { fieldKey } from "./pages/Settings";
import { dateIn } from "./ui";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("error messages", () => {
  it("turns validation errors into plain sentences", () => {
    const msg = detailText([
      { loc: ["body", "phone"], msg: "String should have at most 40 characters" },
      { loc: ["body", "full_name"], msg: "String should have at least 1 character" },
      { loc: ["body", "field_schema", 0, "key"], msg: "String should match pattern '^[a-z]'" },
    ]);
    expect(msg).toBe("Phone: is too long (at most 40 characters). Name: is required. key: has characters that are not allowed");
  });
  it("capitalises server sentences", () => {
    expect(detailText("record this person's consent first")).toBe("Record this person's consent first");
  });
  it("explains non-JSON failures by status", () => {
    expect(statusText(502)).toMatch(/server had a problem/);
    expect(statusText(413)).toMatch(/too large/);
  });
});

describe("api()", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reports a network failure in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/spaces")).rejects.toThrow(OFFLINE);
  });

  it("retries once with a fresh CSRF token", async () => {
    setCsrf("old");
    const fetch = vi.fn()
      .mockResolvedValueOnce(json(403, { detail: "csrf token missing or invalid" }))
      .mockResolvedValueOnce(json(200, { csrf: "new" }))
      .mockResolvedValueOnce(json(201, { ok: true }));
    vi.stubGlobal("fetch", fetch);
    await expect(api("/spaces", { json: { name: "x" } })).resolves.toEqual({ ok: true });
    expect(fetch.mock.calls[2][1].headers["X-CSRF-Token"]).toBe("new");
  });

  it("signs out when the session has ended, but not for a wrong password", async () => {
    const dispatch = vi.fn();
    const store = new Map<string, string>();
    vi.stubGlobal("window", { dispatchEvent: dispatch });
    vi.stubGlobal("sessionStorage", { setItem: (k: string, v: string) => store.set(k, v) });
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => json(401, { detail: "not authenticated" })));
    await expect(api("/spaces")).rejects.toBeInstanceOf(ApiError);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(store.get("fexor:expired")).toBe("1");
    await expect(api("/auth/login", { json: {} })).rejects.toThrow("Not authenticated");
    await expect(api("/auth/password", { json: {} })).rejects.toBeInstanceOf(ApiError);
    expect(dispatch).toHaveBeenCalledTimes(2);  // /auth/password counts, /auth/login doesn't
  });
});

describe("dates", () => {
  it("uses the space's time zone for 'today'", () => {
    const at = new Date("2026-03-01T20:30:00Z");
    expect(dateIn("Asia/Kolkata", at)).toBe("2026-03-02");
    expect(dateIn("America/New_York", at)).toBe("2026-03-01");
    expect(dateIn("Not/AZone", at)).toMatch(/^\d{4}-\d\d-\d\d$/);
  });
});

describe("custom field keys", () => {
  it("makes keys from labels and falls back for other scripts", () => {
    expect(fieldKey("Blood group", 1)).toBe("blood_group");
    expect(fieldKey("2nd language", 1)).toBe("f_2nd_language");
    expect(fieldKey("कक्षा", 3)).toBe("field_3");
  });
});

describe("saving a person", () => {
  it("sends only what changed", () => {
    const before = { full_name: "A", consent: true, start_date: "2026-01-01", fields: { house: "Red", grade: "5" } };
    const after = { ...before, full_name: "B", fields: { house: "Red", grade: "6" } };
    expect(changes(before, after)).toEqual({ full_name: "B", fields: { grade: "6" } });
    expect(changes(before, { ...before, fields: { ...before.fields } })).toEqual({});
  });
});
