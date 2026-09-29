import { create } from "zustand";
import type { InterruptInfo, RunEvent, Usage } from "./events";

export type NodeStatus = "idle" | "running" | "ok" | "error" | "interrupted";

export interface NodeMetrics {
  tokens: number;
  cost: number;
  priced: boolean;
  ms: number;
}

const ZERO: NodeMetrics = { tokens: 0, cost: 0, priced: true, ms: 0 };

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
  metrics: Record<string, NodeMetrics>; // canvas node id -> tokens, cost and time of this run
  deltas: Record<string, Record<string, unknown>>; // canvas node id -> last state update it wrote (pin source)
  usage: Usage | null; // run totals
  durationMs: number | null;
  route: string[]; // canvas node ids in the order they started (saved test cases, M6-T9)
  input: Record<string, unknown> | null; // input of a fresh run (null after resume/continue)
  reset: () => void;
  apply: (ev: RunEvent) => void;
  setHighlight: (ids: string[]) => void;
  setThread: (id: string | null) => void;
  setInput: (input: Record<string, unknown> | null) => void;
}

const empty = {
  status: {}, output: {}, interrupts: [], interrupt: null, finalOutput: null, warnings: [], error: null, breakpoint: false,
  highlight: [], next: [], metrics: {}, usage: null, durationMs: null, deltas: {}, route: [] as string[],
  input: null as Record<string, unknown> | null,
};

export const useRun = create<RunState>((set) => ({
  threadId: null, runId: null, running: false, runSeq: 0, ...empty,
  reset: () => set({ ...empty }),
  apply: (ev) => set((s) => reduce(s, ev)),
  setHighlight: (highlight) => set({ highlight }),
  setThread: (threadId) => set({ threadId, ...empty }),
  setInput: (input) => set({ input }),
}));

/** Pure reducer (unit-tested): how each canonical event changes the canvas. */
export function reduce(s: RunState, ev: RunEvent): Partial<RunState> {
  switch (ev.type) {
    case "RUN_STARTED":
      return { threadId: ev.threadId, runId: ev.runId ?? null, running: true, error: null, interrupt: null, interrupts: [],
        breakpoint: false, finalOutput: null, metrics: {}, usage: null, durationMs: null, route: [] };
    case "STEP_STARTED":
      return {
        status: { ...s.status, [ev.canvas_node_id]: "running" },
        route: s.route.includes(ev.canvas_node_id) ? s.route : [...s.route, ev.canvas_node_id],
      };
    case "STEP_FINISHED": {
      const m = s.metrics[ev.canvas_node_id] ?? ZERO; // a node that runs several times (loops, map) adds up
      return {
        status: { ...s.status, [ev.canvas_node_id]: ev.status },
        metrics: { ...s.metrics, [ev.canvas_node_id]: { ...m, ms: m.ms + (ev.duration_ms ?? 0) } },
      };
    }
    case "STATE_DELTA":
      if (!ev.canvas_node_id) return {};
      return { deltas: { ...s.deltas, [ev.canvas_node_id]: ev.delta } };
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
      if (ev.name === "ac.usage" && typeof ev.value.canvas_node_id === "string") {
        const id = ev.value.canvas_node_id;
        const m = s.metrics[id] ?? ZERO;
        const cost = typeof ev.value.cost_usd === "number" ? ev.value.cost_usd : null;
        const tokens = Number(ev.value.input_tokens ?? 0) + Number(ev.value.output_tokens ?? 0);
        return {
          metrics: { ...s.metrics, [id]: { ...m, tokens: m.tokens + tokens, cost: m.cost + (cost ?? 0), priced: m.priced && cost !== null } },
        };
      }
      return {};
    case "RUN_FINISHED":
      return { running: false, finalOutput: ev.output ?? null, breakpoint: Boolean(ev.breakpoint), next: ev.next ?? [],
        runSeq: s.runSeq + 1, usage: ev.usage ?? null, durationMs: ev.duration_ms ?? null };
    case "RUN_ERROR":
      return { running: false, error: ev.message, runSeq: s.runSeq + 1, usage: ev.usage ?? s.usage };
    default:
      return {};
  }
}

/** "1.2k tok · $0.0031 · 850 ms" (cost omitted when no model had a price). */
export function formatMetrics(m: { tokens: number; cost: number; priced: boolean; ms?: number }): string {
  const parts: string[] = [];
  if (m.tokens) parts.push(`${m.tokens >= 1000 ? `${(m.tokens / 1000).toFixed(1)}k` : m.tokens} tok`);
  if (m.tokens && (m.cost > 0 || m.priced)) parts.push(`$${m.cost < 0.01 ? m.cost.toFixed(4) : m.cost.toFixed(2)}${m.priced ? "" : "+"}`);
  if (m.ms) parts.push(m.ms >= 1000 ? `${(m.ms / 1000).toFixed(1)} s` : `${m.ms} ms`);
  return parts.join(" · ");
}
