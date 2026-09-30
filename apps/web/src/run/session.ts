/** Run actions used by the run panel, timeline, explain view and inbox. Events stream into the run store. */
import { apiFetch, ApiError } from "../api/client";
import { useEditor } from "../editor/store";
import type { RunEvent } from "./events";
import { useRun } from "./runStore";
import { postStream } from "./sse";

/** Apply events once per animation frame: token streams can deliver hundreds of events per second (M4-T3). */
function batcher(apply: (e: RunEvent) => void) {
  let queue: RunEvent[] = [];
  let frame = 0;
  const flush = () => {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    const items = queue;
    queue = [];
    for (const e of items) apply(e);
  };
  return {
    push(e: RunEvent) {
      queue.push(e);
      if (!frame) frame = requestAnimationFrame(flush);
    },
    flush,
  };
}

async function stream(path: string, body: unknown): Promise<void> {
  const run = useRun.getState();
  const b = batcher(run.apply);
  try {
    await postStream(path, body, b.push);
  } catch (e) {
    b.push({ type: "RUN_ERROR", message: e instanceof ApiError ? e.message : String(e) });
  } finally {
    b.flush();
  }
}

/** Pinned outputs and mock models of this viewer (sent with every debug run, M4-T9). */
export function debugAids(): { pins?: Record<string, unknown>; mocks?: Record<string, unknown[]> } {
  const { wf, pins, mocks } = useEditor.getState();
  const known = new Set(wf?.nodes.map((n) => n.id));
  const models = new Set(wf?.resources.filter((r) => r.type === "model.chat").map((r) => r.name));
  const p = Object.fromEntries(Object.entries(pins).filter(([id]) => known.has(id)));
  const m = Object.fromEntries(Object.entries(mocks).filter(([name]) => models.has(name)));
  return { ...(Object.keys(p).length ? { pins: p } : {}), ...(Object.keys(m).length ? { mocks: m } : {}) };
}

export async function startRun(input: Record<string, unknown>, opts: { continueThread?: boolean } = {}) {
  const { wf, breakpoints, flush } = useEditor.getState();
  if (!wf) return;
  await flush();
  const run = useRun.getState();
  const threadId = opts.continueThread ? run.threadId ?? undefined : undefined;
  run.reset();
  const aids = debugAids();
  const debug = breakpoints.length > 0 || Object.keys(aids).length > 0;
  run.setInput(threadId ? null : input); // a continued thread's result depends on its history
  await stream(`/workflows/${wf.id}/runs/stream`, {
    input, thread_id: threadId, ...aids,
    ...(debug ? { mode: "debug", breakpoints: { before: breakpoints } } : {}),
  });
}

export async function resumeRun(value: unknown, interruptId?: string) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  useRun.getState().setInput(null);
  await stream(`/threads/${threadId}/resume`, { value, interrupt_id: interruptId, ...debugAids() });
}

export async function continueRun() {
  const { threadId } = useRun.getState();
  useRun.getState().setInput(null);
  if (threadId) await stream(`/threads/${threadId}/continue`, debugAids());
}

export async function cancelRun() {
  const { runId } = useRun.getState();
  if (runId) await apiFetch(`/runs/${runId}/cancel`, { method: "POST" });
}

export async function forkRun(checkpointId: string, opts: { values?: Record<string, unknown>; asNode?: string; copy?: boolean }) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await stream(`/threads/${threadId}/fork`, {
    checkpoint_id: checkpointId, values: opts.values, as_node: opts.asNode, mode: opts.copy ? "copy" : "branch",
  });
}

export async function editState(checkpointId: string | undefined, values: Record<string, unknown>, asNode?: string) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await apiFetch(`/threads/${threadId}/state`, {
    method: "POST", json: { values, as_node: asNode, checkpoint_id: checkpointId },
  });
  useRun.setState((s) => ({ runSeq: s.runSeq + 1 }));
}
