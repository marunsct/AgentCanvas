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
import { can, ME } from "../api/session";
import { followProject, PROJECT_ID } from "../api/queries";
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
  project_id: string;
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
  unsaved: string | null; // why the last edit is not on the server (refused, or no longer fits); null once a save lands
  connectHint: string | null;
  focusNode: ((id: string) => void) | null;
  tidy: (() => Promise<void>) | null;
  breakpoints: string[]; // canvas node ids; debug runs pause before them (per viewer, remembered locally)
  pins: Record<string, Record<string, unknown>>; // canvas node id -> output replayed in debug runs (M4-T9)
  mocks: Record<string, unknown[]>; // model resource name -> scripted replies for debug runs
  drill: string[]; // subgraph node ids the canvas is showing (read-only drill-in, M2-T13)
  group: (() => Promise<void>) | null; // "convert selection to subgraph", registered by the canvas

  /** `keepEdits`: the reload after a refused save, which keeps the edits queued meanwhile and stays "saving". */
  load: (id: string, opts?: { keepEdits?: boolean }) => Promise<void>;
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
  setPin: (id: string, output: Record<string, unknown> | null) => void;
  setMock: (name: string, replies: unknown[] | null) => void;
  setDrill: (path: string[]) => void;
  setGroup: (fn: (() => Promise<void>) | null) => void;
  frame: (() => void) | null; // "frame the selection" (visual group), registered by the canvas
  setFrame: (fn: (() => void) | null) => void;
  refactor: (kind: "subgraph" | "inline", body: Record<string, unknown>) => Promise<Command[] | null>;
  reset: () => void;
}

let flushTimer: ReturnType<typeof setTimeout> | null = null;
let saving: Promise<void> | null = null; // the commands request in flight
let layoutTimer: ReturnType<typeof setTimeout> | null = null;
let layoutDirty: Layout = {};
const historyMode: { next: "push" | "undo" | "redo" } = { next: "push" };
const savedVersion = new Map<string, number>(); // workflow id -> version after its last saved batch
let loadSeq = 0; // the latest load(): an earlier one that answers later (A -> B, A slower) is ignored

/** Edits not yet on the server (queued, waiting for the debounce, or in flight). */
export function hasUnsavedEdits(): boolean {
  const { pending, inflight } = useEditor.getState();
  return pending.length > 0 || inflight || Object.keys(layoutDirty).length > 0;
}

/**
 * Send what a workflow still has queued after the editor moved on (another workflow opened): reset() used to drop
 * it. Runs after any save in flight, with the version that save produced. Fire and forget.
 */
async function saveLeftBehind(workflowId: string, commands: Command[], version: number, positions: Layout) {
  const before = saving;
  if (before) await before.catch(() => undefined);
  if (commands.length) {
    const at = savedVersion.get(workflowId) ?? version;
    await apiFetch(`/workflows/${workflowId}/commands`, { method: "POST", json: { commands }, version: at })
      .catch((e: Error) => console.warn(`edits to ${workflowId} were not saved:`, e.message));
  }
  if (Object.keys(positions).length) {
    await apiFetch(`/workflows/${workflowId}/layout`, { method: "PUT", json: { positions } }).catch(() => undefined);
  }
}

if (typeof window !== "undefined") {
  // closing the tab with edits still queued asks first (the debounce and the request take a moment)
  window.addEventListener("beforeunload", (e) => {
    if (!hasUnsavedEdits()) return;
    e.preventDefault();
    e.returnValue = "";
  });
}

const initial = {
  workflowId: null, wf: null, version: 0, irHash: "", layout: {}, diagnostics: [], selected: null, pending: [],
  inflight: false, undoStack: [], redoStack: [], notice: null, unsaved: null as string | null, connectHint: null, focusNode: null, tidy: null,
  breakpoints: [] as string[], pins: {} as Record<string, Record<string, unknown>>, mocks: {} as Record<string, unknown[]>,
  drill: [] as string[], group: null, frame: null as (() => void) | null,
};

const bpKey = (id: string) => `agentcanvas.breakpoints.${id}`;
const pinKey = (id: string) => `agentcanvas.pins.${id}`;
const mockKey = (id: string) => `agentcanvas.mocks.${id}`;

function loadLocal<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function saveLocal(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage may be unavailable (private window); the setting still works for this session */
  }
}

function loadBreakpoints(id: string): string[] {
  return loadLocal<string[]>(bpKey(id), []);
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

/**
 * Which edit the server refused and why, e.g. "RenameNode 'n1': id 'x' already exists." (the server names it as
 * `command #i (Op): …`, an index into the batch).
 */
export function describeRefused(batch: Command[], message: string): string {
  const m = /^command #(\d+) \(\w+\): /.exec(message);
  const cmd = m ? batch[Number(m[1])] : batch.length === 1 ? batch[0] : undefined;
  const target = !cmd ? undefined
    : "id" in cmd ? cmd.id : "node" in cmd ? cmd.node.id : "resource" in cmd ? cmd.resource.id : "edge" in cmd ? cmd.edge.id : undefined;
  const reason = m ? message.slice(m[0].length) : message;
  const text = cmd ? `${cmd.op}${target ? ` '${target}'` : ""}: ${reason}` : reason;
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/** Send the queued edits as one commands request. */
async function save(workflowId: string): Promise<void> {
  const get = useEditor.getState, set = useEditor.setState;
  const { pending: batch, version } = get();
  const mode = historyMode.next;
  historyMode.next = "push";
  set({ pending: [], inflight: true });
  try {
    const res = await apiFetch<CommandResult>(`/workflows/${workflowId}/commands`, {
      method: "POST", json: { commands: batch }, version,
    });
    savedVersion.set(workflowId, res.version);
    if (get().workflowId !== workflowId) return; // another workflow was opened meanwhile
    const rest = get().pending; // edits made while the request was in flight are re-applied on top
    const { undoStack, redoStack } = get();
    set({
      version: res.version, unsaved: null,
      irHash: res.ir_hash,
      diagnostics: res.diagnostics,
      wf: rest.length ? applyAll(normalize(res.ir), rest) : normalize(res.ir),
      // an undo or redo step leaves its stack once the server took it (popped before, a refused step was lost)
      undoStack: mode === "undo" ? undoStack.slice(0, -1) : [...undoStack, res.inverse].slice(-200),
      redoStack: mode === "undo" ? [...redoStack, res.inverse] : mode === "redo" ? redoStack.slice(0, -1) : [],
    });
  } catch (e) {
    if (get().workflowId !== workflowId) return;
    const conflict = e instanceof ApiError && e.status === 409;
    // the edits not on the server: on a conflict the whole batch; on a refusal the edits typed after it (a refusal
    // used to drop those too)
    const before = conflict ? [...batch, ...get().pending] : get().pending;
    const refused = conflict ? null : `Edit rejected: ${describeRefused(batch, (e as Error).message)}`;
    set({ pending: [] });
    // still "saving" while the latest version loads: undo and redo wait, and edits typed meanwhile stay queued
    const reloaded = await get().load(workflowId, { keepEdits: true }).then(() => true, () => false);
    if (get().workflowId !== workflowId) return;
    const mine = [...before, ...get().pending]; // a reload used to drop what was typed while it loaded
    historyMode.next = "push"; // the edits put back are new edits, whatever step was queued before
    if (refused) set({ unsaved: refused }); // the badge said "Saved" next to an edit the server had refused
    if (!mine.length) {
      set({ notice: refused ? { kind: "error", text: refused } : { kind: "info", text: "Updated elsewhere — reloaded the latest version." } });
    } else {
      // a 409 used to drop the person's edits: put them back on top of the latest version and save them again
      const latest = get().wf;
      try {
        if (!latest || !reloaded) throw new CommandError("the workflow did not reload");
        set({
          wf: applyAll(latest, mine), pending: mine,
          notice: refused ? { kind: "error", text: refused }
            : { kind: "info", text: "Updated elsewhere — your edits were applied to the latest version." },
        });
        if (flushTimer) clearTimeout(flushTimer);
        flushTimer = setTimeout(() => void get().flush(), 0);
      } catch {
        const text = refused ? `${refused} The edits made after it no longer fit and were not saved.`
          : "Updated elsewhere — your last edits no longer fit the latest version and were not saved. Make them again.";
        set({ notice: { kind: "error", text }, unsaved: text });
      }
    }
  } finally {
    if (get().workflowId === workflowId) set({ inflight: false });
  }
}

/** Undo or redo presses waiting for the edits being saved; each runs once the save before it has settled. */
const historyQueue: ("undo" | "redo")[] = [];
let historyDraining = false;

/** Dispatch the last undo (or redo) step; the step leaves its stack when it is saved. */
function step(kind: "undo" | "redo") {
  const { undoStack, redoStack, dispatch } = useEditor.getState();
  const stack = kind === "undo" ? undoStack : redoStack;
  const batch = stack[stack.length - 1];
  if (batch) dispatch(batch, { history: kind });
}

function queueHistory(kind: "undo" | "redo") {
  const { pending, inflight } = useEditor.getState();
  if (!historyQueue.length && !pending.length && !inflight) {
    step(kind);
    return;
  }
  historyQueue.push(kind);
  if (historyDraining) return;
  historyDraining = true;
  const workflowId = useEditor.getState().workflowId;
  void (async () => {
    try {
      while (historyQueue.length) {
        await useEditor.getState().flush();
        const s = useEditor.getState();
        if (s.workflowId !== workflowId || s.pending.length || s.inflight) break; // another workflow, or a save that failed
        step(historyQueue.shift()!);
      }
    } finally {
      historyQueue.length = 0;
      historyDraining = false;
    }
  })();
}

export const useEditor = create<EditorState>((set, get) => ({
  ...initial,

  reset: () => {
    const { workflowId, pending, version } = get();
    if (flushTimer) clearTimeout(flushTimer);
    if (layoutTimer) clearTimeout(layoutTimer);
    flushTimer = layoutTimer = null;
    if (workflowId && (pending.length || Object.keys(layoutDirty).length)) {
      void saveLeftBehind(workflowId, pending, version, layoutDirty); // queued edits are saved, not dropped
    }
    layoutDirty = {};
    loadSeq++; // a load still on its way is for the workflow left behind
    set({ ...initial });
  },

  load: async (id, opts = {}) => {
    const seq = ++loadSeq;
    const out = await apiFetch<WorkflowOut>(`/workflows/${id}`);
    if (seq !== loadSeq) return;
    // the editor reads blocks, models and settings of the app's project: open another project's workflow in it
    if (out.project_id !== PROJECT_ID && followProject(out.project_id)) return;
    const { workflowId, version, undoStack, redoStack } = get();
    const same = workflowId === id && version === out.version; // a reload after a refused edit keeps the history
    set({
      workflowId: id, wf: normalize(out.ir), version: out.version, irHash: out.ir_hash, layout: out.layout ?? {},
      diagnostics: out.diagnostics, ...(opts.keepEdits ? {} : { pending: [], inflight: false, unsaved: null }), breakpoints: loadBreakpoints(id),
      pins: loadLocal(pinKey(id), {}), mocks: loadLocal(mockKey(id), {}), drill: [],
      // inverses from another version no longer apply
      undoStack: same ? undoStack : [], redoStack: same ? redoStack : [],
    });
  },

  dispatch: (cmds, opts = {}) => {
    const { wf, pending } = get();
    if (!wf || cmds.length === 0) return;
    if (!can("builder")) {
      set({ notice: { kind: "info", text: `View only: your role (${ME.role}) cannot edit workflows.` } });
      return;
    }
    try {
      const next = applyAll(wf, cmds);
      set({ wf: next, pending: cmds.reduce(coalesce, pending), notice: null });
    } catch (e) {
      if (!(e instanceof CommandError)) return;
      // an undo or redo step that no longer applies to the workflow never will: it leaves its stack
      const { undoStack, redoStack } = get();
      const drop = opts.history === "undo" ? { undoStack: undoStack.slice(0, -1) }
        : opts.history === "redo" ? { redoStack: redoStack.slice(0, -1) } : {};
      set({ notice: { kind: "error", text: e.message }, ...drop });
      return;
    }
    historyMode.next = opts.history ?? "push";
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = setTimeout(() => void get().flush(), opts.debounceMs ?? 0);
  },

  flush: async () => {
    while (saving) await saving; // callers that run or refactor need the edits saved, not only sent
    const { workflowId, pending } = get();
    if (!workflowId || pending.length === 0) return;
    saving = save(workflowId).finally(() => {
      saving = null;
    });
    await saving;
    if (get().pending.length) await get().flush(); // edits made meanwhile
  },

  // queued edits land first, then the step (history stays linear); a press while saving used to be ignored
  undo: () => queueHistory("undo"),
  redo: () => queueHistory("redo"),

  select: (id) => set({ selected: id }),

  setPositions: (positions, persist = false) => {
    set({ layout: { ...get().layout, ...positions } });
    if (!persist || !can("builder")) return;
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
      if (workflowId) {
        void apiFetch(`/workflows/${workflowId}/layout`, { method: "PUT", json: { positions: body } }).catch((e: Error) => {
          if (get().workflowId === workflowId) set({ notice: { kind: "error", text: `Positions not saved: ${e.message}` } });
        });
      }
    };
    layoutTimer = setTimeout(save, 400);
  },

  setNotice: (notice) => set({ notice }),
  setConnectHint: (connectHint) => set({ connectHint }),
  setFocusNode: (focusNode) => set({ focusNode }),
  setTidy: (tidy) => set({ tidy }),
  setDrill: (drill) => set({ drill, selected: null }),
  setGroup: (group) => set({ group }),
  setFrame: (frame) => set({ frame }),
  refactor: async (kind, body) => {
    const { workflowId, flush } = get();
    if (!workflowId || !can("builder")) return null;
    await flush(); // the server computes the refactoring from the saved workflow
    try {
      const out = await apiFetch<{ commands: Command[] }>(`/workflows/${workflowId}/refactor/${kind}`, { method: "POST", json: body });
      get().dispatch(out.commands);
      return out.commands;
    } catch (e) {
      set({ notice: { kind: "error", text: (e as Error).message } });
      return null;
    }
  },
  setPin: (id, output) => {
    const { pins, workflowId } = get();
    const next = { ...pins };
    if (output) next[id] = output;
    else delete next[id];
    set({ pins: next });
    if (workflowId) saveLocal(pinKey(workflowId), next);
  },
  setMock: (name, replies) => {
    const { mocks, workflowId } = get();
    const next = { ...mocks };
    if (replies && replies.length) next[name] = replies;
    else delete next[name];
    set({ mocks: next });
    if (workflowId) saveLocal(mockKey(workflowId), next);
  },
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
