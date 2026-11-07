/** Run actions used by the run panel, timeline, explain view and inbox. Events stream into the run store. */
import { apiFetch, ApiError } from "../api/client";
import { can } from "../api/session";
import { useEditor } from "../editor/store";
import { useUi } from "../editor/ui";
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

/** The one run stream the run store listens to; a newer run, or leaving the editor, stops reading it. */
let active: AbortController | null = null;

/** Stop reading the current run's events (the run goes on on the server; reopening the workflow shows it). */
export function stopStreaming(): void {
  active?.abort();
  active = null;
}

async function stream(path: string, body: unknown): Promise<void> {
  const run = useRun.getState();
  stopStreaming();
  const mine = new AbortController();
  active = mine;
  // events of a stream that was stopped (another run, another workflow, A -> Home -> A) are dropped
  const b = batcher((e) => { if (active === mine) run.apply(e); });
  try {
    await postStream(path, body, b.push, mine.signal);
  } catch (e) {
    if (!mine.signal.aborted) b.push({ type: "RUN_ERROR", message: e instanceof ApiError ? e.message : String(e) });
  } finally {
    b.flush();
    if (active === mine) {
      active = null;
      // the stream ended without RUN_FINISHED or RUN_ERROR (API restart, proxy idle timeout): the panel used to stay
      // "Running…" with Run, chat and Stop dead until a reload
      if (useRun.getState().running) {
        run.apply({ type: "RUN_ERROR", message: "Connection lost — the run goes on on the server. Reopen the workflow to see how it ended." });
      }
    }
  }
}

/** Pinned outputs and mock models of this viewer (sent with every debug run, M4-T9). Only builders may debug: a
 * reviewer approving a step with Dry run ticked, or with pins left in this browser, was refused with 403. */
export function debugAids(): { pins?: Record<string, unknown>; mocks?: Record<string, unknown[]>; dry_run?: boolean } {
  if (!can("builder")) return {};
  const { wf, pins, mocks } = useEditor.getState();
  const known = new Set(wf?.nodes.map((n) => n.id));
  const models = new Set(wf?.resources.filter((r) => r.type === "model.chat").map((r) => r.name));
  const p = Object.fromEntries(Object.entries(pins).filter(([id]) => known.has(id)));
  const m = Object.fromEntries(Object.entries(mocks).filter(([name]) => models.has(name)));
  return { ...(Object.keys(p).length ? { pins: p } : {}), ...(Object.keys(m).length ? { mocks: m } : {}) , ...(useUi.getState().dryRun ? { dry_run: true } : {}) };
}

/** Run `body` unless a run is already being started: a double-click on Run, Send, Approve, Continue or Fork sent two
 * requests (`running` only arrives with the first event), and the second one stopped reading the first's stream and
 * showed a false 409. */
async function once(body: () => Promise<void>): Promise<void> {
  if (useRun.getState().starting) return;
  useRun.setState({ starting: true });
  try {
    await body();
  } finally {
    useRun.setState({ starting: false });
  }
}

export async function startRun(input: Record<string, unknown>, opts: { continueThread?: boolean } = {}) {
  const { wf, breakpoints, flush } = useEditor.getState();
  if (!wf) return;
  await once(async () => {
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
  });
}

export async function resumeRun(value: unknown, interruptId?: string) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await once(async () => {
    useRun.getState().setInput(null);
    await stream(`/threads/${threadId}/resume`, { value, interrupt_id: interruptId, ...debugAids() });
  });
}

export async function continueRun() {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await once(async () => {
    useRun.getState().setInput(null);
    await stream(`/threads/${threadId}/continue`, debugAids());
  });
}

export async function cancelRun() {
  const { runId } = useRun.getState();
  if (runId) await apiFetch(`/runs/${runId}/cancel`, { method: "POST" });
}

export async function forkRun(checkpointId: string, opts: { values?: Record<string, unknown>; asNode?: string; copy?: boolean }) {
  const { threadId } = useRun.getState();
  if (!threadId) return;
  await once(() => {
    // a fork replays from an earlier checkpoint: the node colours, streamed text and costs of the run it replaces
    // would otherwise stay on the canvas next to the new branch's
    useRun.getState().reset();
    return stream(`/threads/${threadId}/fork`, {
      checkpoint_id: checkpointId, values: opts.values, as_node: opts.asNode, mode: opts.copy ? "copy" : "branch",
    });
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
