import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, detailText, OFFLINE, setCsrf, statusText } from "./api";
import { changes } from "./pages/People";
import { clockPlus } from "./pages/Schedule";
import { fieldKey, thresholdAdvice } from "./pages/Settings";
import { dateIn, fmtClock, fmtTime } from "./ui";

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

describe("shift times", () => {
  it("adds and subtracts minutes across midnight", () => {
    expect(clockPlus("09:00", 30)).toBe("09:30");
    expect(clockPlus("17:00", -15)).toBe("16:45");
    expect(clockPlus("23:50", 20)).toBe("00:10");
    expect(clockPlus("00:10", -20)).toBe("23:50");
  });
});

describe("time format", () => {
  it("shows the space's 12- or 24-hour clock", () => {
    expect(fmtClock("16:05", "12h")).toBe("4:05 PM");
    expect(fmtClock("00:30", "12h")).toBe("12:30 AM");
    expect(fmtClock("16:05", "24h")).toBe("16:05");
    expect(fmtTime("2026-11-10T16:05:00Z", "UTC", "12h")).toMatch(/^4:05\s?PM$/i);
    expect(fmtTime("2026-11-10T16:05:00Z", "UTC", "24h")).toBe("16:05");
  });
});

describe("face match threshold advice", () => {
  const base = {
    days: 90, threshold: 0.42, confirmed_false_rejects: 0, unconfirmed_mismatches: 0,
    genuine: { count: 0 }, impostor: { count: 0 }, genuine_below_current: 0,
    suggested_threshold: null, genuine_below_suggested: null, min_samples: { genuine: 20, impostor: 30 },
  };

  it("waits for enough check-ins before suggesting", () => {
    const a = thresholdAdvice({ ...base, genuine: { count: 5 }, impostor: { count: 4 } });
    expect(a.suggested).toBeNull();
    expect(a.text).toBe("Not enough kiosk checks yet to suggest a value: 5 of 20 face matches and 4 of 30 closest-other scores so far.");
  });

  it("suggests a more lenient value when honest people are turned away", () => {
    const a = thresholdAdvice({ ...base, genuine: { count: 40 }, impostor: { count: 40, max: 0.3 },
      suggested_threshold: 0.36, genuine_below_current: 3, genuine_below_suggested: 0, confirmed_false_rejects: 3 });
    expect(a.suggested).toBe(0.36);
    expect(a.color).toBe("yellow");
    expect(a.text).toBe("3 honest face matches scored below the current 0.42 (3 confirmed by an admin's review). At 0.36 that would be 0. The closest other person scored 0.30.");
  });

  it("warns when someone else came close to the current value", () => {
    const a = thresholdAdvice({ ...base, genuine: { count: 40 }, impostor: { count: 40, max: 0.45 },
      suggested_threshold: 0.48, genuine_below_current: 0, genuine_below_suggested: 2 });
    expect(a.color).toBe("red");
    expect(a.text).toBe("Another person in this space scored 0.45, close to or above the current 0.42. A stricter 0.48 is safer; 2 honest face matches would have been below it.");
  });

  it("says when the current value fits", () => {
    const a = thresholdAdvice({ ...base, genuine: { count: 40 }, impostor: { count: 40, max: 0.39 },
      suggested_threshold: 0.42, genuine_below_current: 0, genuine_below_suggested: 0 });
    expect(a.suggested).toBeNull();
    expect(a.color).toBe("green");
  });
});
