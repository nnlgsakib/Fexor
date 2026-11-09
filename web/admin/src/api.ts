let csrf = "";

export function setCsrf(token: string | null | undefined) {
  csrf = token ?? "";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Opts = {
  method?: string;
  json?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
};

const FIELD_NAMES: Record<string, string> = {
  full_name: "Name", external_id: "ID", email: "Email", phone: "Phone", title: "Title",
  start_date: "Start date", end_date: "End date", name: "Name", url: "URL", password: "Password",
  new_password: "New password", start_time: "Start time", end_time: "End time", day: "Date",
  member_label: "What to call people", country: "Country", timezone: "Time zone",
};

/** Turns a FastAPI error body into a sentence a non-technical admin can act on. */
export function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail.charAt(0).toUpperCase() + detail.slice(1);
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc: unknown[] = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body" && x !== "query") : [];
        const field = loc.length ? String(loc[loc.length - 1]) : "";
        const name = FIELD_NAMES[field] ?? (field && Number.isNaN(Number(field)) ? field.replaceAll("_", " ") : "");
        let msg = String(d?.msg ?? "is not valid").replace(/^Value error, /, "");
        if (/at most (\d+) characters/.test(msg)) msg = `is too long (${msg.match(/at most (\d+)/)![0]} characters)`;
        else if (/at least 1 character/.test(msg)) msg = "is required";
        else if (/should match pattern/.test(msg)) msg = "has characters that are not allowed";
        return name ? `${name}: ${msg}` : msg;
      })
      .join(". ");
  }
  return "Something went wrong";
}

/** Message for a response that isn't Fexor JSON (proxy error page, server down…). */
export function statusText(status: number): string {
  if (status === 401) return "Your session has ended. Please sign in again.";
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "Not found. It may have been removed.";
  if (status === 413) return "That file is too large.";
  if (status === 429) return "Too many attempts. Wait a minute and try again.";
  if (status >= 500) return "The server had a problem. Try again in a moment; if it keeps happening, restart the server.";
  return `Request failed (${status})`;
}

export const OFFLINE = "Can't reach the server. Check the network connection and try again.";

export function url(path: string, query?: Opts["query"]) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return `/api${path}${s ? `?${s}` : ""}`;
}

// Sign-in itself returns 401 for a wrong password: that is not "session ended".
const NO_LOGOUT = ["/auth/login", "/auth/mfa", "/auth/me", "/setup"];

async function send(path: string, opts: Opts, method: string): Promise<Response> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  try {
    return await fetch(url(path, opts.query), { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, OFFLINE);
  }
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.json !== undefined || opts.form ? "POST" : "GET");
  let r = await send(path, opts, method);
  if (r.status === 403 && method !== "GET" && (await r.clone().text()).includes("csrf")) {
    // The session was renewed elsewhere (another tab signed in): pick up the new token, retry once.
    const me = await send("/auth/me", {}, "GET");
    if (me.ok) {
      setCsrf((await me.json()).csrf);
      r = await send(path, opts, method);
    }
  }
  if (r.status === 204) return undefined as T;
  const isJson = r.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const detail = isJson ? (data as { detail?: unknown }).detail : undefined;
    const msg = detail !== undefined ? detailText(detail) : statusText(r.status);
    if (r.status === 401 && !NO_LOGOUT.some((p) => path.startsWith(p))) {
      try { sessionStorage.setItem("fexor:expired", "1"); } catch { /* private mode */ }
      window.dispatchEvent(new Event("fexor:logout"));
    }
    throw new ApiError(r.status, msg);
  }
  return data as T;
}
