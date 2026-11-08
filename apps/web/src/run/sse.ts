import type { RunEvent } from "./events";
import { API_BASE, authFetch, failed } from "../api/client";

import { parseMessage as parseFrames } from "./sseFrames";

/** One SSE message's events: its `data:` lines, each a JSON event. A malformed line is skipped, not fatal. */
export function parseMessage(message: string): RunEvent[] {
  return parseFrames<RunEvent>(message);
}

/**
 * POST + read a Server-Sent-Events stream (EventSource only supports GET, so we use fetch).
 * `signal` stops reading (leaving the editor, a newer run): the run itself goes on on the server.
 */
export async function postStream(
  path: string, body: unknown, onEvent: (e: RunEvent) => void, signal?: AbortSignal,
): Promise<void> {
  const res = await authFetch(`${API_BASE}${path}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!res.ok || !res.body) throw await failed(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const stop = () => void reader.cancel().catch(() => undefined);
  signal?.addEventListener("abort", stop, { once: true });
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (signal?.aborted) break;
      if (done) { // a connection that closed right after the last line: its events still count
        for (const e of parseMessage(buffer)) onEvent(e);
        break;
      }
      buffer += value;
      let sep: RegExpExecArray | null;
      while ((sep = /\r?\n\r?\n/.exec(buffer))) { // one SSE message ends with a blank line
        const message = buffer.slice(0, sep.index);
        buffer = buffer.slice(sep.index + sep[0].length);
        for (const e of parseMessage(message)) onEvent(e);
      }
    }
  } finally {
    signal?.removeEventListener("abort", stop);
  }
}

/** GET + read a Server-Sent-Events stream whose messages are JSON (the presence channel): the same reader as
 * `postStream`, for a stream that is not a run. Resolves when the connection closes or `signal` aborts. */
export async function getStream<T>(path: string, onMessage: (m: T) => void, signal?: AbortSignal): Promise<void> {
  const res = await authFetch(`${API_BASE}${path}`, { headers: { accept: "text/event-stream" }, signal });
  if (!res.ok || !res.body) throw await failed(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const stop = () => void reader.cancel().catch(() => undefined);
  signal?.addEventListener("abort", stop, { once: true });
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done || signal?.aborted) break;
      buffer += value;
      let sep: RegExpExecArray | null;
      while ((sep = /\r?\n\r?\n/.exec(buffer))) {
        const message = buffer.slice(0, sep.index);
        buffer = buffer.slice(sep.index + sep[0].length);
        for (const e of parseMessage(message)) onMessage(e as unknown as T);
      }
    }
  } finally {
    signal?.removeEventListener("abort", stop);
  }
}
