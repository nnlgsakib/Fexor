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

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== "body").join(".") : "";
        return loc ? `${loc}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  return "Request failed";
}

export function url(path: string, query?: Opts["query"]) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return `/api${path}${s ? `?${s}` : ""}`;
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? (opts.json !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const r = await fetch(url(path, opts.query), { method, headers, body, credentials: "same-origin" });
  if (r.status === 204) return undefined as T;
  const isJson = r.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await r.json() : await r.text();
  if (!r.ok) {
    const msg = isJson ? detailText((data as { detail?: unknown }).detail) : String(data || r.statusText);
    if (r.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("fexor:logout"));
    throw new ApiError(r.status, msg);
  }
  return data as T;
}
