import { create } from "zustand";
import type { RunEvent } from "./ir";

export type NodeStatus = "idle" | "running" | "ok" | "error" | "interrupted";
export interface PendingInterrupt { id: string; payload: unknown }

interface RunState {
  threadId: string | null;
  status: Record<string, NodeStatus>;         // canvas node id -> status
  output: Record<string, string>;             // streamed text per node
  interrupt: PendingInterrupt | null;
  error: string | null;
  running: boolean;
  reset: () => void;
  apply: (ev: RunEvent) => void;
}

export const useRun = create<RunState>((set) => ({
  threadId: null, status: {}, output: {}, interrupt: null, error: null, running: false,
  reset: () => set({ status: {}, output: {}, interrupt: null, error: null }),
  apply: (ev) => set((s) => reduce(s, ev)),
}));

/** Pure reducer (unit-tested): how each canonical event changes the canvas. */
export function reduce(s: RunState, ev: RunEvent): Partial<RunState> {
  switch (ev.type) {
    case "RUN_STARTED": return { threadId: ev.threadId, running: true, error: null, interrupt: null };
    case "STEP_STARTED": return { status: { ...s.status, [ev.canvas_node_id]: "running" } };
    case "STEP_FINISHED": return { status: { ...s.status, [ev.canvas_node_id]: ev.status } };
    case "TEXT_MESSAGE_CONTENT":
      if (!ev.canvas_node_id) return {};
      return { output: { ...s.output, [ev.canvas_node_id]: (s.output[ev.canvas_node_id] ?? "") + ev.delta } };
    case "CUSTOM":
      if (ev.name === "ac.interrupt" && ev.value.id) return { interrupt: { id: ev.value.id, payload: ev.value.payload } };
      return {};
    case "RUN_FINISHED": return { running: false };
    case "RUN_ERROR": return { running: false, error: ev.message };
    default: return {};
  }
}
