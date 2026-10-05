/** Thin fetch wrapper for the AgentCanvas API: JSON in/out, If-Match versions, problem+json errors. */
import { accessToken, signIn } from "./auth";

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  errors?: unknown[];
  [k: string]: unknown;
}

export class ApiError extends Error {
  constructor(public status: number, public problem: Problem) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
  }
}

export const API_BASE = "/api/v1";

/** Headers with the signed-in user's bearer token (none in dev mode). */
export function withAuth(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

/** A 401 means the session ended: sign in again (no-op in dev mode). */
export async function failed(res: Response): Promise<ApiError> {
  if (res.status === 401) void signIn();
  return new ApiError(res.status, (await res.json().catch(() => ({}))) as Problem);
}

export async function apiFetch<T>(path: string, init: RequestInit & { version?: number; json?: unknown } = {}): Promise<T> {
  const headers = withAuth(init.headers);
  if (init.json !== undefined) headers.set("content-type", "application/json");
  if (init.version !== undefined) headers.set("If-Match", `"${init.version}"`);
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (!res.ok) throw await failed(res);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? await res.json() : await res.text()) as T;
}

/** Download a file the API serves (a plain link would not carry the bearer token). */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { headers: withAuth() });
  if (!res.ok) throw await failed(res);
  const name = /filename="?([^";]+)"?/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
