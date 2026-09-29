/**
 * Editor state (Zustand). The IR is the model; React Flow nodes/edges are derived from it.
 *
 * Edits are commands: applied locally at once (optimistic), queued, and sent to
 * `POST /v1/workflows/{id}/commands` with `If-Match`. The server answer is authoritative: it carries the
 * new version, the validated IR, diagnostics and the inverse batch (used for undo). On 409 the editor
 * reloads and tells the user (build-guide/06 §2, ticket M2-T3).
 */
import { create } from "zustand";
import { ApiError, apiFetch } from "../api/client";
import type { Diagnostic, Layout, Position } from "../api/types";
import { applyAll, applyLocal, type Command, CommandError, composePatches } from "../ir/commands";
import { normalize, type WF } from "../ir/model";
import type * as G from "../ir/generated";

export interface Notice {
  kind: "info" | "error";
  text: string;
}

interface WorkflowOut {
  id: string;
  name: string;
  version: number;
  ir_hash: string;
  ir: G.Workflow;
  layout: Layout;
  diagnostics: Diagnostic[];
}

interface CommandResult extends WorkflowOut {
  inverse: Command[];
}

export interface EditorState {
  workflowId: string | null;
  wf: WF | null;
  version: number;
  irHash: string;
  layout: Layout;
  diagnostics: Diagnostic[];
  selected: string | null; // node or resource id
  pending: Command[];
  inflight: boolean;
  undoStack: Command[][];
  redoStack: Command[][];
  notice: Notice | null;
  connectHint: string | null;
  focusNode: ((id: string) => void) | null;
  tidy: (() => Promise<void>) | null;
  breakpoints: string[]; // canvas node ids; debug runs pause before them (per viewer, remembered locally)

  load: (id: string) => Promise<void>;
  dispatch: (cmds: Command[], opts?: { debounceMs?: number; history?: "push" | "undo" | "redo" }) => void;
  flush: () => Promise<void>;
  undo: () => void;
  redo: () => void;
  select: (id: string | null) => void;
  setPositions: (positions: Layout, persist?: boolean) => void;
  setNotice: (n: Notice | null) => void;
  setConnectHint: (h: string | null) => void;
  setFocusNode: (fn: ((id: string) => void) | null) => void;
  setTidy: (fn: (() => Promise<void>) | null) => void;
  toggleBreakpoint: (id: string) => void;
  reset: () => void;
}

let flushTimer: ReturnType<typeof setTimeout> | null = null;
let layoutTimer: ReturnType<typeof setTimeout> | null = null;
let layoutDirty: Layout = {};
const historyMode: { next: "push" | "undo" | "redo" } = { next: "push" };

const initial = {
  workflowId: null, wf: null, version: 0, irHash: "", layout: {}, diagnostics: [], selected: null, pending: [],
  inflight: false, undoStack: [], redoStack: [], notice: null, connectHint: null, focusNode: null, tidy: null,
  breakpoints: [] as string[],
};

const bpKey = (id: string) => `agentcanvas.breakpoints.${id}`;

function loadBreakpoints(id: string): string[] {
  try {
    const raw = localStorage.getItem(bpKey(id));
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

/** Merge a new UpdateNode/UpdateResource into the last queued one for the same element (typing in a form). */
export function coalesce(pending: Command[], cmd: Command): Command[] {
  const last = pending[pending.length - 1];
  if (last && (cmd.op === "UpdateNode" || cmd.op === "UpdateResource") && last.op === cmd.op && last.id === cmd.id) {
    const patch = composePatches(last.patch, cmd.patch);
    if (patch !== undefined) return [...pending.slice(0, -1), { ...last, patch: patch as typeof cmd.patch }];
  }
  return [...pending, cmd];
}

export const useEditor = create<EditorState>((set, get) => ({
  ...initial,

  reset: () => set({ ...initial }),

  load: async (id) => {
    const out = await apiFetch<WorkflowOut>(`/workflows/${id}`);
    set({
      workflowId: id, wf: normalize(out.ir), version: out.version, irHash: out.ir_hash, layout: out.layout ?? {},
      diagnostics: out.diagnostics, pending: [], inflight: false, breakpoints: loadBreakpoints(id),
    });
  },

  dispatch: (cmds, opts = {}) => {
    const { wf, pending } = get();
    if (!wf || cmds.length === 0) return;
    try {
      const next = applyAll(wf, cmds);
      set({ wf: next, pending: cmds.reduce(coalesce, pending), notice: null });
    } catch (e) {
      if (e instanceof CommandError) set({ notice: { kind: "error", text: e.message } });
      return;
    }
    historyMode.next = opts.history ?? "push";
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = setTimeout(() => void get().flush(), opts.debounceMs ?? 0);
  },

  flush: async () => {
    const { workflowId, pending, inflight, version } = get();
    if (!workflowId || inflight || pending.length === 0) return;
    const batch = pending;
    const mode = historyMode.next;
    historyMode.next = "push";
    set({ pending: [], inflight: true });
    try {
      const res = await apiFetch<CommandResult>(`/workflows/${workflowId}/commands`, {
        method: "POST", json: { commands: batch }, version,
      });
      const rest = get().pending; // edits made while the request was in flight are re-applied on top
      const { undoStack, redoStack } = get();
      set({
        version: res.version,
        irHash: res.ir_hash,
        diagnostics: res.diagnostics,
        wf: rest.length ? applyAll(normalize(res.ir), rest) : normalize(res.ir),
        undoStack: mode === "undo" ? undoStack : [...undoStack, res.inverse].slice(-200),
        redoStack: mode === "undo" ? [...redoStack, res.inverse] : mode === "redo" ? redoStack : [],
      });
    } catch (e) {
      const conflict = e instanceof ApiError && e.status === 409;
      const text = conflict ? "Updated elsewhere — reloaded the latest version." : `Edit rejected: ${(e as Error).message}`;
      set({ pending: [], notice: { kind: conflict ? "info" : "error", text } });
      await get().load(workflowId).catch(() => undefined);
    } finally {
      set({ inflight: false });
      if (get().pending.length) void get().flush();
    }
  },

  undo: () => {
    const { undoStack, pending, inflight } = get();
    if (pending.length || inflight) return; // let queued edits land first; history stays linear
    const batch = undoStack[undoStack.length - 1];
    if (!batch) return;
    set({ undoStack: undoStack.slice(0, -1) });
    get().dispatch(batch, { history: "undo" });
  },

  redo: () => {
    const { redoStack, pending, inflight } = get();
    if (pending.length || inflight) return;
    const batch = redoStack[redoStack.length - 1];
    if (!batch) return;
    set({ redoStack: redoStack.slice(0, -1) });
    get().dispatch(batch, { history: "redo" });
  },

  select: (id) => set({ selected: id }),

  setPositions: (positions, persist = false) => {
    set({ layout: { ...get().layout, ...positions } });
    if (!persist) return;
    layoutDirty = { ...layoutDirty, ...positions };
    if (layoutTimer) clearTimeout(layoutTimer);
    const save = () => {
      const { workflowId, pending, inflight } = get();
      // the server drops positions of elements it does not know yet: let queued AddNode/AddResource land first
      if (pending.length || inflight) {
        layoutTimer = setTimeout(save, 150);
        return;
      }
      const body = layoutDirty;
      layoutDirty = {};
      if (workflowId) void apiFetch(`/workflows/${workflowId}/layout`, { method: "PUT", json: { positions: body } });
    };
    layoutTimer = setTimeout(save, 400);
  },

  setNotice: (notice) => set({ notice }),
  setConnectHint: (connectHint) => set({ connectHint }),
  setFocusNode: (focusNode) => set({ focusNode }),
  setTidy: (tidy) => set({ tidy }),
  toggleBreakpoint: (id) => {
    const { breakpoints, workflowId } = get();
    const next = breakpoints.includes(id) ? breakpoints.filter((b) => b !== id) : [...breakpoints, id];
    set({ breakpoints: next });
    try {
      if (workflowId) localStorage.setItem(bpKey(workflowId), JSON.stringify(next));
    } catch {
      /* storage may be unavailable (private window); breakpoints still work for this session */
    }
  },
}));

export function positionOf(layout: Layout, id: string): Position | undefined {
  return layout[id];
}

export { applyLocal };
