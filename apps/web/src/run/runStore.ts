import { create } from "zustand";
import type { InterruptInfo, RunEvent } from "./events";

export type NodeStatus = "idle" | "running" | "ok" | "error" | "interrupted";

export interface RunState {
  threadId: string | null;
  runId: string | null;
  status: Record<string, NodeStatus>; // canvas node id -> status
  output: Record<string, string>; // streamed text per node
  interrupts: InterruptInfo[];
  interrupt: InterruptInfo | null; // first pending interrupt (convenience)
  finalOutput: Record<string, unknown> | null;
  warnings: string[];
  error: string | null;
  running: boolean;
  breakpoint: boolean;
  runSeq: number; // increments whenever a run ends (timeline/explain/chat refetch)
  highlight: string[]; // node ids emphasised by the timeline/explain views
  next: string[];
  reset: () => void;
  apply: (ev: RunEvent) => void;
  setHighlight: (ids: string[]) => void;
  setThread: (id: string | null) => void;
}

const empty = {
  status: {}, output: {}, interrupts: [], interrupt: null, finalOutput: null, warnings: [], error: null, breakpoint: false,
  highlight: [], next: [],
};

export const useRun = create<RunState>((set) => ({
  threadId: null, runId: null, running: false, runSeq: 0, ...empty,
  reset: () => set({ ...empty }),
  apply: (ev) => set((s) => reduce(s, ev)),
  setHighlight: (highlight) => set({ highlight }),
  setThread: (threadId) => set({ threadId, ...empty }),
}));

/** Pure reducer (unit-tested): how each canonical event changes the canvas. */
export function reduce(s: RunState, ev: RunEvent): Partial<RunState> {
  switch (ev.type) {
    case "RUN_STARTED":
      return { threadId: ev.threadId, runId: ev.runId ?? null, running: true, error: null, interrupt: null, interrupts: [],
        breakpoint: false, finalOutput: null };
    case "STEP_STARTED":
      return { status: { ...s.status, [ev.canvas_node_id]: "running" } };
    case "STEP_FINISHED":
      return { status: { ...s.status, [ev.canvas_node_id]: ev.status } };
    case "TEXT_MESSAGE_CONTENT":
      if (!ev.canvas_node_id) return {};
      return { output: { ...s.output, [ev.canvas_node_id]: (s.output[ev.canvas_node_id] ?? "") + ev.delta } };
    case "CUSTOM":
      if (ev.name === "ac.interrupt" && typeof ev.value.id === "string") {
        const intr = ev.value as unknown as InterruptInfo;
        const interrupts = [...s.interrupts.filter((i) => i.id !== intr.id), intr];
        return { interrupts, interrupt: interrupts[0] };
      }
      if (ev.name === "ac.warning") return { warnings: [...s.warnings, String(ev.value.message ?? "")] };
      return {};
    case "RUN_FINISHED":
      return { running: false, finalOutput: ev.output ?? null, breakpoint: Boolean(ev.breakpoint), next: ev.next ?? [],
        runSeq: s.runSeq + 1 };
    case "RUN_ERROR":
      return { running: false, error: ev.message, runSeq: s.runSeq + 1 };
    default:
      return {};
  }
}
