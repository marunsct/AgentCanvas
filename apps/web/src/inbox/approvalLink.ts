/** Approve and Deny links from approval notifications (F-9): the public API the confirmation page calls. The link's
 * token is the capability; it travels in the address's fragment (`/approve#<token>`), which never reaches a server, and
 * in the body of POSTs, never in an address. */
import { API_BASE } from "../api/client";

// typed here until the generated API types are regenerated
export interface LinkInfo {
  interrupt_id: string;
  action: "approve" | "deny";
  kind: string;
  status: "pending" | "decided" | "expired" | "cancelled" | string;
  workflow_name: string;
  project_name: string;
  step: string;
  deciders: string;
  recipient: string | null;
  expires_at: string;
  decided_at: string | null;
  sign_in: boolean;
  why_sign_in: string | null;
  inbox_path: string;
}

export class LinkError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** The token after `#` in the page's address. */
export function tokenFromHash(hash: string = location.hash): string {
  return decodeURIComponent(hash.replace(/^#/, "").replace(/^t=/, "")).trim();
}

/** The Inbox item a token names, read from its (unsecret, signed) body: a link that no longer works can still lead
 * to the item. */
export function itemOf(token: string): string | null {
  try {
    const body = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    const data = JSON.parse(atob(body + "=".repeat((4 - (body.length % 4)) % 4))) as { i?: unknown };
    return typeof data.i === "string" && /^[\w-]+$/.test(data.i) ? data.i : null;
  } catch {
    return null;
  }
}

async function post<T>(path: string, json: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/approval-links/${path}`, {
    method: "POST", credentials: "omit", headers: { "content-type": "application/json" }, body: JSON.stringify(json),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new LinkError(res.status, typeof body.detail === "string" ? body.detail : `the server answered HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** What a link is for. Changes nothing. */
export const inspectLink = (token: string) => post<LinkInfo>("inspect", { token });

/** Decide with a link sent to one person. */
export const decideByLink = (token: string, reason?: string) =>
  post<{ run_id: string; action: string }>("decision", { token, ...(reason ? { reason } : {}) });
