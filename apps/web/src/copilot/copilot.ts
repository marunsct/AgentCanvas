/** Copilot state (S14): the conversation, the live answer, and the proposal previewed on the canvas. */
import { create } from "zustand";
import { apiFetch } from "../api/client";
import type * as G from "../ir/generated";
import type { Command } from "../ir/commands";
import { normalize, type WF } from "../ir/model";
import { useEditor } from "../editor/store";
import { postStream } from "../run/sse";

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

export interface ChatMessage { role: "user" | "assistant"; content: string; proposal_id?: string; mode?: Mode }

export interface Activity { name: string; ok: boolean; error?: string | null; summary?: string | null }

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
  | { type: "start"; model: string; origin: string }
  | { type: "text"; text: string }
  | { type: "tool"; name: string; ok: boolean; error?: string | null; summary?: string | null }
  | { type: "brief"; brief: Brief }
  | { type: "usage" | "done"; usage: Conversation["usage"]; cap: number }
  | { type: "proposal"; proposal: Proposal }
  | { type: "error"; message: string };

/** What the canvas draws while a proposal is open: the workflow as it would be, with the changes marked. */
export interface Ghost { workflow: WF; diff: ProposalDiff }

interface CopilotState {
  workflowId: string | null;
  conversation: Conversation | null;
  busy: boolean;
  live: { text: string[]; activity: Activity[] } | null; // the answer being streamed
  error: string | null;
  ghost: Ghost | null;
  request: { text: string; mode: Mode } | null; // "Ask the Copilot" from elsewhere in the editor (S15)
  askFor: (text: string, mode: Mode) => void;
  load: (workflowId: string) => Promise<void>;
  send: (text: string, mode: Mode) => Promise<void>;
  accept: () => Promise<void>;
  reject: () => Promise<void>;
  newChat: () => Promise<void>;
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
};
export const toolLabel = (name: string) => TOOL_LABELS[name] ?? name;

function ghostOf(p: Proposal | null): Ghost | null {
  if (!p || p.status !== "pending" || p.stale || !p.preview || !p.diff) return null;
  return { workflow: normalize(p.preview), diff: p.diff };
}

export const useCopilot = create<CopilotState>((set, get) => ({
  workflowId: null,
  conversation: null,
  busy: false,
  live: null,
  error: null,
  ghost: null,
  request: null,
  askFor: (text, mode) => set({ request: { text, mode } }),

  load: async (workflowId) => {
    set({ workflowId, error: null });
    const conversation = await apiFetch<Conversation>(`/workflows/${workflowId}/copilot`);
    if (get().workflowId !== workflowId) return;
    set({ conversation, ghost: ghostOf(conversation.pending) });
  },

  send: async (text, mode) => {
    const { workflowId, busy } = get();
    if (!workflowId || busy || !text.trim()) return;
    await useEditor.getState().flush(); // the Copilot reads the saved draft
    const conv = get().conversation;
    set({
      busy: true, error: null, ghost: null, live: { text: [], activity: [] },
      conversation: conv && { ...conv, messages: [...conv.messages, { role: "user", content: text, mode }] },
    });
    const onEvent = (raw: unknown) => {
      const e = raw as Event;
      const live = get().live ?? { text: [], activity: [] };
      const c = get().conversation;
      switch (e.type) {
        case "text": set({ live: { ...live, text: [...live.text, e.text] } }); break;
        case "tool": set({ live: { ...live, activity: [...live.activity, e] } }); break;
        case "brief": if (c) set({ conversation: { ...c, brief: e.brief } }); break;
        case "usage": case "done": if (c) set({ conversation: { ...c, usage: e.usage } }); break;
        case "proposal": set({ ghost: ghostOf(e.proposal) }); break;
        case "error": set({ error: e.message }); break;
      }
    };
    try {
      await postStream(`/workflows/${workflowId}/copilot/messages`, { text, mode }, onEvent as never);
    } catch (e) {
      set({ error: (e as Error).message });
    } finally {
      set({ busy: false, live: null });
      await get().load(workflowId).catch(() => undefined);
    }
  },

  accept: async () => {
    const { conversation, workflowId } = get();
    const p = conversation?.pending;
    if (!p || !workflowId) return;
    if (p.commands?.length) {
      const editor = useEditor.getState();
      // edits made since the proposal are fine while the commands still apply; otherwise the edit is refused
      editor.dispatch(p.commands);
      await editor.flush();
      if (useEditor.getState().notice?.kind === "error") return;
    }
    set({ ghost: null });
    await apiFetch(`/workflows/${workflowId}/copilot/proposals/${p.id}`, { method: "POST", json: { status: "accepted" } });
    await get().load(workflowId);
  },

  reject: async () => {
    const { conversation, workflowId } = get();
    const p = conversation?.pending;
    if (!p || !workflowId) return;
    set({ ghost: null });
    await apiFetch(`/workflows/${workflowId}/copilot/proposals/${p.id}`, { method: "POST", json: { status: "rejected" } });
    await get().load(workflowId);
  },

  newChat: async () => {
    const { workflowId } = get();
    if (!workflowId) return;
    await apiFetch(`/workflows/${workflowId}/copilot`, { method: "DELETE" });
    set({ ghost: null, error: null });
    await get().load(workflowId);
  },
}));
