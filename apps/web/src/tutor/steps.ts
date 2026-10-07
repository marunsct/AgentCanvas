/** Guided build (FR-X-24): the steps of the first-run tutorial, each checked against the real workflow. Pure. */
import type { WF } from "../ir/model";

export interface TutorRun {
  finished: boolean; // some run of this tour reached RUN_FINISHED
  interrupted: boolean; // some run of this tour paused for a human
}

export interface TutorStep {
  id: string;
  title: string;
  concept: string; // the idea this step teaches, in one or two sentences
  todo: string; // what to do on the canvas
  done: (wf: WF, run: TutorRun) => boolean;
}

const nodesOf = (wf: WF, type: string) => wf.nodes.filter((n) => n.type === type);
/** Steps reachable from `id` along flow edges (`id` included). */
function reachable(wf: WF, id: string): Set<string> {
  const seen = new Set([id]);
  const queue = [id];
  while (queue.length) {
    const at = queue.shift()!;
    for (const e of wf.edges) {
      if (e.kind === "flow" && e.source.node === at && !seen.has(e.target.node)) {
        seen.add(e.target.node);
        queue.push(e.target.node);
      }
    }
  }
  return seen;
}
/** True when `id` lies on a connected flow path from START to END. */
const onRunPath = (wf: WF, id: string) => reachable(wf, "START").has(id) && reachable(wf, id).has("END");

export const STEPS: TutorStep[] = [
  {
    id: "state",
    title: "Give the workflow a memory",
    concept: "Data fields are the workflow's notepad: named boxes every step can read and write. (Technically: state channels; a field's reducer decides whether a new value replaces the old one or is appended.)",
    todo: "Click empty canvas, then in the right panel press “+ Channel” twice and name the fields question and answer.",
    done: (wf) => ["question", "answer"].every((c) => wf.state.channels.some((ch) => ch.name === c)),
  },
  {
    id: "llm",
    title: "Add a step that thinks",
    concept: "Steps are the blocks. An “Ask AI” step sends your instructions (the prompt) to an AI model and saves the reply in a data field.",
    todo: "Click the dot on the right of ▶ Start and choose “Ask AI” (or drag it from the left). In the right panel set the instructions to {{ state.question }} and “Save the reply in” to answer.",
    done: (wf) => nodesOf(wf, "core.llm").some((n) => n.config?.output === "answer" && String(n.config?.prompt ?? "").includes("state.question")),
  },
  {
    id: "flow",
    title: "Connect the flow",
    concept: "Arrows decide what runs next. A run begins at ▶ Start and is done when it reaches ■ Finish.",
    todo: "Connect ▶ Start to your Ask AI step (if it isn't yet), then click the Ask AI step's right-hand dot and choose Finish.",
    done: (wf) => nodesOf(wf, "core.llm").some((n) => onRunPath(wf, n.id)),
  },
  {
    id: "model",
    title: "Plug in a model",
    concept: "Add-ons (AI models, tools, memory) plug into steps instead of running on their own. One AI model can power several steps.",
    todo: "Click the coloured “model” square on the Ask AI step and choose “AI model”. It is added and plugged in for you.",
    done: (wf) => {
      const models = new Set(wf.resources.filter((r) => r.type === "model.chat").map((r) => r.id));
      const llms = new Set(nodesOf(wf, "core.llm").map((n) => n.id));
      return wf.edges.some((e) => e.kind === "wiring" && models.has(e.source.node) && llms.has(e.target.node));
    },
  },
  {
    id: "run",
    title: "Run it",
    concept: "A run tries the workflow with real input. Every step is saved as it happens, so you can look back at (and replay) any point in the Timeline below.",
    todo: "Open the Run tab, enter {\"question\": \"What is a graph?\"} and press ▶ Run.",
    done: (_wf, run) => run.finished,
  },
  {
    id: "approval",
    title: "Put a human in the loop",
    concept: "An “Ask for approval” step pauses the run until a person approves or rejects in the Run tab or the Inbox; the run then continues exactly where it stopped.",
    todo: "Add an “Ask for approval” step between Ask AI and Finish (what the person reviews: answer), then run again and approve it.",
    done: (wf, run) => nodesOf(wf, "core.approval").some((n) => onRunPath(wf, n.id)) && run.interrupted,
  },
];

/** Index of the first step that is not done yet (STEPS.length when the tour is complete). */
export function currentStep(wf: WF, run: TutorRun): number {
  const i = STEPS.findIndex((s) => !s.done(wf, run));
  return i === -1 ? STEPS.length : i;
}
