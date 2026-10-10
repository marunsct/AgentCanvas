/** Share links (S23): the public chat page's talk with the API. No sign-in: the link's token is the capability, so
 * this module never sends the signed-in user's token (it does not use the authenticated client). */

export interface ShareInfo {
  title: string;
  greeting: string;
  suggestions: string[];
  input: { field?: string | null; chat?: boolean };
}

export interface ShareQuestion { id: string | null; kind: "approval" | "tool_approval" | "input" | string; question: string }

/** The events a share link's run stream carries (a filtered view of the run's events). */
export type ShareEvent =
  | { type: "RUN_STARTED"; runId: string; threadId: string }
  | { type: "TEXT_MESSAGE_CONTENT"; delta: string }
  | { type: "RUN_FINISHED"; runId: string; threadId: string; status: string; answer: string; interrupts: ShareQuestion[] }
  | { type: "RUN_ERROR"; runId?: string; message: string };

export class ShareError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const BASE = "/api/v1/share";
const VISITOR_KEY = "agentcanvas.share.visitor";

/** The share link's token in a page path like /s/sh_abc (null when the path is not a share page). */
export function tokenFromPath(path: string): string | null {
  const m = /^\/s\/([A-Za-z0-9_-]{4,200})\/?$/.exec(path);
  return m ? m[1] : null;
}

export function isEmbed(search: string): boolean {
  return new URLSearchParams(search).get("embed") === "1";
}

function randomId(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

let memoryVisitor: string | null = null;

/** This browser's visitor secret: only it continues the conversations it started (kept in localStorage, or for
 * this page only when storage is blocked, e.g. in some third-party iframes). */
export function visitorId(): string {
  try {
    const stored = localStorage.getItem(VISITOR_KEY);
    if (stored && /^[A-Za-z0-9_-]{16,128}$/.test(stored)) return stored;
    const fresh = randomId();
    localStorage.setItem(VISITOR_KEY, fresh);
    return fresh;
  } catch {
    memoryVisitor ??= randomId();
    return memoryVisitor;
  }
}

export function friendly(status: number, detail?: string): string {
  if (status === 404) return "This link does not work any more. Ask the person who shared it for a new one.";
  if (status === 429) return "Too many messages right now. Please wait a minute and try again.";
  if (status === 422 && detail) return detail;
  return "Something went wrong. Please try again.";
}

async function problem(res: Response): Promise<ShareError> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return new ShareError(res.status, friendly(res.status, typeof body.detail === "string" ? body.detail : undefined));
}

export async function loadShare(token: string): Promise<ShareInfo> {
  const res = await fetch(`${BASE}/${encodeURIComponent(token)}`, { credentials: "omit" });
  if (!res.ok) throw await problem(res);
  return (await res.json()) as ShareInfo;
}

/** Feed SSE text to `onEvent`; returns what is left over (an incomplete message). */
export function parseSse(buffer: string, onEvent: (e: ShareEvent) => void): string {
  let sep: number;
  while ((sep = buffer.indexOf("\n\n")) >= 0) {
    const message = buffer.slice(0, sep);
    buffer = buffer.slice(sep + 2);
    for (const line of message.split("\n")) {
      if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)) as ShareEvent);
    }
  }
  return buffer;
}

async function stream(path: string, body: unknown, onEvent: (e: ShareEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${BASE}/${path}`, {
    method: "POST", credentials: "omit", signal,
    headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw await problem(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer = parseSse(buffer + value, onEvent);
  }
}

export function sendMessage(
  token: string, message: string, threadId: string | null, onEvent: (e: ShareEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const body = { message, visitor: visitorId(), ...(threadId ? { thread_id: threadId } : {}) };
  return stream(`${encodeURIComponent(token)}/runs/stream`, body, onEvent, signal);
}

export function answerQuestion(
  token: string, threadId: string, q: ShareQuestion, answer: { approve?: boolean; text?: string },
  onEvent: (e: ShareEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const body = { visitor: visitorId(), interrupt_id: q.id, ...answer };
  return stream(`${encodeURIComponent(token)}/threads/${encodeURIComponent(threadId)}/resume`, body, onEvent, signal);
}

export async function rate(token: string, runId: string, rating: "up" | "down"): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(token)}/runs/${encodeURIComponent(runId)}/feedback`, {
    method: "POST", credentials: "omit", headers: { "content-type": "application/json" },
    body: JSON.stringify({ rating, visitor: visitorId() }),
  });
  if (!res.ok) throw await problem(res);
}

/** The snippet a site adds to show the chat widget. */
export function embedSnippet(origin: string, token: string): string {
  return `<script src="${origin}/widget.js" data-share="${token}" async></script>`;
}
