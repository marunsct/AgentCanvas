/** Thin fetch wrapper for the AgentCanvas API: JSON in/out, If-Match versions, problem+json errors. */

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

export async function apiFetch<T>(path: string, init: RequestInit & { version?: number; json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.json !== undefined) headers.set("content-type", "application/json");
  if (init.version !== undefined) headers.set("If-Match", `"${init.version}"`);
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))) as Problem);
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? await res.json() : await res.text()) as T;
}
