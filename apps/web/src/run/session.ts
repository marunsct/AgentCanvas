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

export async function startRun(input: Record<string, unknown>, opts: { continueThread?: boolean } = {}) {
  const { wf, breakpoints, flush } = useEditor.getState();
  if (!wf) return;
  await flush();
  const run = useRun.getState();
  const threadId = opts.continueThread ? run.threadId ?? undefined : undefined;
  run.reset();
  const debug = breakpoints.length > 0;
  await stream(`/workflows/${wf.id}/runs/stream`, {
    input, thread_id: threadId, ...(debug ? { mode: "debug", breakpoints: { before: breakpoints } } : {}),
  });
}

export async function resumeRun(value: unknown, interruptId?: string) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await stream(`/threads/${threadId}/resume`, { value, interrupt_id: interruptId });
}

export async function continueRun() {
  const { threadId } = useRun.getState();
  if (threadId) await stream(`/threads/${threadId}/continue`, {});
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
