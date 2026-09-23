/** عميل REST: رمز الوصول في الذاكرة فقط (ليس localStorage)، وتحديث تلقائي عبر كوكي HttpOnly (13-security). */
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, any> = {}) {
    super(message);
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onUnauthenticated: (() => void) | null = null;

export const setAccessToken = (t: string | null) => { accessToken = t; };
export const hasToken = () => accessToken !== null;
export const onAuthLost = (cb: () => void) => { onUnauthenticated = cb; };

async function refresh(): Promise<boolean> {
  refreshing ??= fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then(async (r) => {
      if (!r.ok) return false;
      accessToken = (await r.json()).access_token;
      return true;
    })
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

export async function tryRestoreSession(): Promise<boolean> {
  return refresh();
}

type Opts = { method?: string; body?: unknown; form?: FormData; params?: Record<string, any>; headers?: Record<string, string>; raw?: boolean };

export async function api<T = any>(path: string, opts: Opts = {}, retry = true): Promise<T> {
  const url = new URL(`/api/v1${path}`, window.location.origin);
  Object.entries(opts.params ?? {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    (Array.isArray(v) ? v : [v]).forEach((x) => url.searchParams.append(k, String(x)));
  });
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, {
    method: opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET"),
    headers, credentials: "same-origin",
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
  });
  if (res.status === 401 && retry && !path.startsWith("/auth/login")) {
    if (await refresh()) return api<T>(path, opts, false);
    accessToken = null;
    onUnauthenticated?.();
  }
  if (!res.ok) {
    let p: any = {};
    try { p = await res.json(); } catch { /* غير JSON */ }
    throw new ApiError(res.status, p.code ?? "ERROR", p.title ?? `خطأ ${res.status}`, p.details ?? {});
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function download(path: string, body: unknown, params: Record<string, any> = {}) {
  const res = await api<Response>(path, { method: "POST", body, params, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = decodeURIComponent(cd.match(/filename\*=UTF-8''([^;]+)/)?.[1] ?? "report");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function downloadGet(path: string, params: Record<string, any> = {}) {
  const res = await api<Response>(path, { params, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const name = decodeURIComponent(cd.match(/filename\*=UTF-8''([^;]+)/)?.[1] ?? "file");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}
