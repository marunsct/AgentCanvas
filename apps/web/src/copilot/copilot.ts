/** Copilot state (S14): the conversation, the live answer, and the proposal previewed on the canvas. */
import { create } from "zustand";
import { apiFetch } from "../api/client";
import type * as G from "../ir/generated";
import type { Command } from "../ir/commands";
import { normalize, type WF } from "../ir/model";
import { useEditor } from "../editor/store";
import { postStream } from "../run/sse";
import { dependencies, preview, type ReviewDiff, toggle } from "./review";

export type Mode = "build" | "ask";

export interface Brief {
  goal: string;
  inputs?: string[];
  outputs?: string[];
  steps?: string[];
  tools?: string[];
  success_criteria?: string[];
  open_questions?: string[];
}

export interface ProposalDiff { added: string[]; changed: string[]; removed: string[] }

export interface ProposedTest { name: string; input: Record<string, unknown>; expect?: Record<string, unknown> }

export interface Proposal {
  id: string;
  summary: string;
  commands?: Command[];
  tests?: ProposedTest[];
  notes?: string[];
  base_version: number;
  status: "pending" | "accepted" | "rejected" | "superseded";
  errors: number;
  diff?: ProposalDiff;
  preview?: G.Workflow;
  stale?: boolean;
  problem?: string;
}

export interface ChatMessage { role: "user" | "assistant"; content: string; proposal_id?: string; mode?: Mode; stopped?: boolean }

export interface Activity { name: string; ok: boolean; error?: string | null; summary?: string | null }

/** One item of the plan the Copilot keeps while it works (Deep Agents' to-do list). */
export interface Todo { content: string; status: "pending" | "in_progress" | "completed" }

/** The answer being streamed: finished paragraphs, the one being written, what it did, its plan. */
export interface Live { text: string[]; streaming: string; activity: Activity[]; todos: Todo[] }

const NO_LIVE: Live = { text: [], streaming: "", activity: [], todos: [] };

export interface Conversation {
  id: string | null;
  enabled: boolean;
  messages: ChatMessage[];
  brief: Brief | null;
  proposals: Proposal[];
  pending: Proposal | null;
  usage: { input_tokens?: number; output_tokens?: number };
  token_cap: number;
  model: { model?: string | null; origin?: string | null; problem?: string | null };
}

type Event =
  | { type: "start"; model: string; origin: string; engine?: string }
  | { type: "text"; text: string }
  | { type: "text_delta"; text: string }
  | { type: "todos"; todos: Todo[] }
  | { type: "tool"; name: string; ok: boolean; error?: string | null; summary?: string | null }
  | { type: "brief"; brief: Brief }
  | { type: "usage" | "done"; usage: Conversation["usage"]; cap: number; cost_usd?: number | null }
  | { type: "proposal"; proposal: Proposal }
  | { type: "error"; message: string };

/** What the canvas draws while a proposal is open: the workflow as it would be, with the changes marked. */
export interface Ghost { workflow: WF; diff: ReviewDiff }

/** What the person keeps of the open proposal: which changes, which tests, and whether the notes. */
export interface Selection { changes: Set<number>; tests: Set<number>; notes: boolean }

interface CopilotState {
  workflowId: string | null;
  conversation: Conversation | null;
  busy: boolean;
  live: Live | null; // the answer being streamed
  error: string | null;
  last: { text: string; mode: Mode } | null; // the last message sent, for Retry
  cost: number | null; // what this conversation has cost, when the model's price is known (from this session's turns)
  ghost: Ghost | null;
  selection: Selection | null;
  toggleChange: (i: number) => void;
  toggleTest: (i: number) => void;
  toggleNotes: () => void;
  request: { text: string; mode: Mode } | null; // "Ask the Copilot" from elsewhere in the editor (S15)
  askFor: (text: string, mode: Mode) => void;
  load: (workflowId: string) => Promise<void>;
  send: (text: string, mode: Mode) => Promise<void>;
  stop: () => void;
  retry: () => Promise<void>;
  accept: () => Promise<void>;
  reject: () => Promise<void>;
  newChat: () => Promise<void>;
  openThread: (id: string) => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
}

const TOOL_LABELS: Record<string, string> = {
  get_workflow: "Read the workflow",
  find_blocks: "Looked for blocks",
  get_block: "Read a block's settings",
  get_template: "Read a template",
  set_brief: "Wrote the brief",
  propose_changes: "Proposed changes",
  get_problems: "Checked the problems",
  list_runs: "Looked at recent runs",
  get_run: "Read a run",
  search_guide: "Searched the guide",
  list_models: "Looked at the models",
  run_test: "Ran a test",
  reproduce_run: "Reran a past run",
  propose_tests: "Proposed tests",
  save_note: "Proposed a note",
  write_todos: "Updated its plan",
  task: "Asked a helper",
  read_file: "Read a file",
};
export const toolLabel = (name: string) => TOOL_LABELS[name] ?? name;

function ghostOf(p: Proposal | null, sel?: Selection | null): Ghost | null {
  if (!p || p.status !== "pending" || p.stale || !p.preview || !p.diff) return null;
  const wf = useEditor.getState().wf;
  if (!wf || !p.commands) return { workflow: normalize(p.preview), diff: { ...p.diff, removed: p.diff.removed ?? [] } };
  // drawn from the draft as it is now, with the ticked changes, so the canvas shows what Accept would do
  return preview(wf, p.commands, sel?.changes ?? new Set(p.commands.keys()));
}

function allOf(p: Proposal | null): Selection | null {
  if (!p || p.status !== "pending" || p.stale) return null;
  return { changes: new Set((p.commands ?? []).keys()), tests: new Set((p.tests ?? []).keys()), notes: true };
}

/** The Copilot answer being read; leaving the editor stops reading it (the server finishes the turn). */
let copilotStream: AbortController | null = null;

export function stopCopilotStream(): void {
  copilotStream?.abort();
  copilotStream = null;
}

export const useCopilot = create<CopilotState>((set, get) => ({
  workflowId: null,
  conversation: null,
  busy: false,
  live: null,
  error: null,
  last: null,
  cost: null,
  ghost: null,
  selection: null,
  request: null,
  toggleChange: (i) => {
    const { conversation, selection } = get();
    const p = conversation?.pending;
    if (!p?.commands || !selection) return;
    const changes = toggle(selection.changes, i, dependencies(p.commands));
    const next = { ...selection, changes };
    set({ selection: next, ghost: ghostOf(p, next) });
  },
  toggleTest: (i) => {
    const selection = get().selection;
    if (!selection) return;
    const tests = new Set(selection.tests);
    if (tests.has(i)) tests.delete(i); else tests.add(i);
    set({ selection: { ...selection, tests } });
  },
  toggleNotes: () => {
    const selection = get().selection;
    if (selection) set({ selection: { ...selection, notes: !selection.notes } });
  },
  askFor: (text, mode) => set({ request: { text, mode } }),

  load: async (workflowId) => {
    const open = useEditor.getState().workflowId;
    if (open && open !== workflowId) return; // a late answer about a workflow the editor has left
    set({ workflowId, error: null, ...(get().workflowId !== workflowId ? { cost: null } : {}) });
    const conversation = await apiFetch<Conversation>(`/workflows/${workflowId}/copilot`);
    if (get().workflowId !== workflowId) return;
    const selection = allOf(conversation.pending);
    set({ conversation, selection, ghost: ghostOf(conversation.pending, selection) });
  },

  send: async (text, mode) => {
    const { workflowId, busy } = get();
    if (!workflowId || busy || !text.trim()) return;
    await useEditor.getState().flush(); // the Copilot reads the saved draft
    const conv = get().conversation;
    set({
      busy: true, error: null, ghost: null, live: NO_LIVE, last: { text, mode },
      conversation: conv && { ...conv, messages: [...conv.messages, { role: "user", content: text, mode }] },
    });
    const onEvent = (raw: unknown) => {
      if (get().workflowId !== workflowId) return; // the editor moved to another workflow meanwhile
      const e = raw as Event;
      const live = get().live ?? NO_LIVE;
      const c = get().conversation;
      switch (e.type) {
        case "text_delta": set({ live: { ...live, streaming: live.streaming + e.text } }); break;
        case "text": set({ live: { ...live, text: [...live.text, e.text], streaming: "" } }); break;
        case "todos": set({ live: { ...live, todos: e.todos } }); break;
        case "tool": set({ live: { ...live, activity: [...live.activity, e] } }); break;
        case "brief": if (c) set({ conversation: { ...c, brief: e.brief } }); break;
        case "usage": case "done": if (c) set({ conversation: { ...c, usage: e.usage }, cost: e.cost_usd ?? get().cost }); break;
        case "proposal": { const selection = allOf(e.proposal); set({ selection, ghost: ghostOf(e.proposal, selection) }); break; }
        case "error": set({ error: e.message }); break;
      }
    };
    copilotStream?.abort();
    const mine = (copilotStream = new AbortController());
    try {
      await postStream(`/workflows/${workflowId}/copilot/messages`, { text, mode }, onEvent as never, mine.signal);
    } catch (e) {
      if (get().workflowId === workflowId && !mine.signal.aborted) set({ error: (e as Error).message });
    } finally {
      if (copilotStream === mine) copilotStream = null;
      if (get().workflowId === workflowId) {
        // after Stop the server saves the part already said as the connection closes: give it a moment
        if (mine.signal.aborted) await new Promise((r) => setTimeout(r, 400));
        set({ busy: false, live: null });
        await get().load(workflowId).catch(() => undefined);
      }
    }
  },

  stop: () => {
    // the server keeps what was said so far, marked as stopped; the reload in send() shows it
    stopCopilotStream();
  },

  retry: async () => {
    const last = get().last;
    if (last) await get().send(last.text, last.mode);
  },

  accept: async () => {
    const { conversation, workflowId, selection } = get();
    const p = conversation?.pending;
    if (!p || !workflowId) return;
    const keep = selection ?? allOf(p)!;
    const commands = (p.commands ?? []).filter((_, i) => keep.changes.has(i));
    if (commands.length) {
      const editor = useEditor.getState();
      if (editor.workflowId !== workflowId) return;
      await editor.flush(); // earlier edits first, so the version below moves only with these commands
      const before = useEditor.getState().version;
      editor.setNotice(null);
      // edits made since the proposal are fine while the commands still apply; otherwise the edit is refused
      editor.dispatch(commands);
      await editor.flush();
      const after = useEditor.getState();
      // refused (error), not allowed (view only) or lost to someone else's save (conflict): not accepted
      if (after.notice || after.version === before) return;
    }
    set({ ghost: null, selection: null });
    await apiFetch(`/workflows/${workflowId}/copilot/proposals/${p.id}`, {
      method: "POST",
      json: { status: "accepted", commands: [...keep.changes].sort((a, b) => a - b), tests: [...keep.tests].sort((a, b) => a - b), notes: keep.notes },
    });
    await get().load(workflowId);
  },

  reject: async () => {
    const { conversation, workflowId } = get();
    const p = conversation?.pending;
    if (!p || !workflowId) return;
    set({ ghost: null, selection: null });
    await apiFetch(`/workflows/${workflowId}/copilot/proposals/${p.id}`, { method: "POST", json: { status: "rejected" } });
    await get().load(workflowId);
  },

  openThread: async (id) => {
    const { workflowId } = get();
    if (!workflowId) return;
    await apiFetch(`/workflows/${workflowId}/copilot/threads/${id}/open`, { method: "POST" });
    set({ ghost: null, selection: null, error: null, cost: null });
    await get().load(workflowId);
  },

  deleteThread: async (id) => {
    const { workflowId } = get();
    if (!workflowId) return;
    await apiFetch(`/workflows/${workflowId}/copilot/threads/${id}`, { method: "DELETE" });
    await get().load(workflowId);
  },

  newChat: async () => {
    const { workflowId } = get();
    if (!workflowId) return;
    await apiFetch(`/workflows/${workflowId}/copilot`, { method: "DELETE" });
    set({ ghost: null, error: null, cost: null });
    await get().load(workflowId);
  },
}));
