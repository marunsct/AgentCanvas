/** Thin fetch wrapper for the AgentCanvas API: JSON in/out, If-Match versions, problem+json errors. */
import { accessToken, renew, signInAgain } from "./auth";

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

/** A 401 that renewing could not fix means the session ended: sign in again (see `signInAgain`; no-op in dev mode). */
export async function failed(res: Response): Promise<ApiError> {
  if (res.status === 401) signInAgain();
  return new ApiError(res.status, (await res.json().catch(() => ({}))) as Problem);
}

/** `fetch` with the bearer token; on 401 renew the token once without leaving the page and retry. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(url, { ...init, headers: withAuth(init.headers) });
  if (res.status !== 401 || !(await renew())) return res;
  return fetch(url, { ...init, headers: withAuth(init.headers) });
}

/** How long a plain (non-streaming) call may take before the page stops waiting: a server that never answers must
 * end in an error the person can see, not a spinner for ever. Exports and uploads can be slow, hence generous. */
export const REQUEST_TIMEOUT_MS = 120_000;

export async function apiFetch<T>(
  path: string, init: RequestInit & { version?: number; json?: unknown; timeoutMs?: number } = {},
): Promise<T> {
  const { timeoutMs = REQUEST_TIMEOUT_MS, json, version, ...rest } = init;
  const headers = new Headers(rest.headers);
  if (json !== undefined) headers.set("content-type", "application/json");
  if (version !== undefined) headers.set("If-Match", `"${version}"`);
  const timeout = AbortSignal.timeout(timeoutMs);
  try {
    const res = await authFetch(`${API_BASE}${path}`, {
      ...rest,
      headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      signal: rest.signal ? AbortSignal.any([rest.signal, timeout]) : timeout,
    });
    if (!res.ok) throw await failed(res);
    if (res.status === 204) return undefined as T;
    const type = res.headers.get("content-type") ?? "";
    return (type.includes("json") ? await res.json() : await res.text()) as T;
  } catch (e) {
    if (timeout.aborted && !rest.signal?.aborted && !(e instanceof ApiError)) {
      throw new ApiError(0, { title: "No answer", detail: `The server did not answer within ${Math.round(timeoutMs / 1000)} seconds. Try again.` });
    }
    throw e;
  }
}

/** Download a file the API serves (a plain link would not carry the bearer token). */
export async function apiDownload(path: string, fallbackName: string): Promise<void> {
  const res = await authFetch(`${API_BASE}${path}`);
  if (!res.ok) throw await failed(res);
  const name = /filename="?([^";]+)"?/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
