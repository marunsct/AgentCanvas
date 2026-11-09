import { feed, parseMessage } from "../run/sseFrames";
import type { FileRef } from "../ir/files";

/** Share links (S23): the public chat page's talk with the API. No sign-in: the link's token is the capability, so
 * this module never sends the signed-in user's token (it does not use the authenticated client). */

/** The file field a visitor may attach files to (G-5), when the workflow has one. */
export interface ShareAttachments { field: string; accept?: string | null; multiple: boolean }

export interface ShareInfo {
  title: string;
  greeting: string;
  suggestions: string[];
  input: { field?: string | null; chat?: boolean };
  attachments?: ShareAttachments | null;
  frame_origins?: string[]; // the sites allowed to frame the page (G-15): the postMessage API answers only these
  ai_notice?: string; // tells visitors they are chatting with an AI (AI Act Art. 50): always shown
}

/** What the page says when the server sent no wording of its own (an older server). */
export const DEFAULT_AI_NOTICE = "You are chatting with an AI assistant. Answers can be wrong.";

/** A message of the visitor's saved conversation (G-15). */
export interface SavedMessage {
  role: "user" | "assistant";
  text: string;
  run_id?: string | null;
  error?: boolean;
  attached?: string[];
  question?: ShareQuestion | null;
}

/** The visitor's latest conversation on this link, kept on the server; `busy`: its last message is still being answered. */
export interface SavedConversation { thread_id: string | null; busy: boolean; messages: SavedMessage[] }

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

/** The conversation this browser's visitor secret last had on the link (empty when there is none). */
export async function loadConversation(token: string): Promise<SavedConversation> {
  const res = await fetch(`${BASE}/${encodeURIComponent(token)}/conversation`, {
    credentials: "omit", headers: { "x-visitor": visitorId() },
  });
  if (!res.ok) throw await problem(res);
  return (await res.json()) as SavedConversation;
}

/** Start over for good: the saved conversation is closed, so a reload does not bring it back. */
export async function closeConversation(token: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(token)}/conversation`, {
    method: "DELETE", credentials: "omit", headers: { "x-visitor": visitorId() },
  });
  if (!res.ok) throw await problem(res);
}

/** Feed SSE text to `onEvent`; returns what is left over (an incomplete message). */
export function parseSse(buffer: string, onEvent: (e: ShareEvent) => void): string {
  return feed<ShareEvent>(buffer, onEvent); // the run streams' framing: \r\n line ends too (a proxy may rewrite them)
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
    if (done) { // a connection that closed right after the last line: its events (the answer's end) still count
      for (const e of parseMessage<ShareEvent>(buffer)) onEvent(e);
      break;
    }
    buffer = parseSse(buffer + value, onEvent);
  }
}

/** Send a file to the link (the visitor's own: only this browser's conversations can use it). */
export async function uploadShareFile(token: string, file: File): Promise<FileRef> {
  const form = new FormData();
  form.append("visitor", visitorId());
  form.append("file", file);
  const res = await fetch(`${BASE}/${encodeURIComponent(token)}/files`, { method: "POST", credentials: "omit", body: form });
  if (!res.ok) throw await problem(res);
  return (await res.json()) as FileRef;
}

export function sendMessage(
  token: string, message: string, threadId: string | null, onEvent: (e: ShareEvent) => void, signal?: AbortSignal,
  files: FileRef[] = [],
): Promise<void> {
  const body = {
    message, visitor: visitorId(), ...(threadId ? { thread_id: threadId } : {}),
    ...(files.length ? { files: files.map((f) => f.id) } : {}),
  };
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
