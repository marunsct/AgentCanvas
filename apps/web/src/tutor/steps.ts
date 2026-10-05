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
const flowFrom = (wf: WF, id: string) => wf.edges.some((e) => e.kind === "flow" && e.source.node === id);
const flowTo = (wf: WF, id: string) => wf.edges.some((e) => e.kind === "flow" && e.target.node === id);

export const STEPS: TutorStep[] = [
  {
    id: "state",
    title: "Give the workflow a memory",
    concept: "State is the data every step reads and writes. Each field is a channel; its reducer decides what happens when steps write to it (replace, append…).",
    todo: "In the Inspector (click empty canvas) add a channel named question, then one named answer.",
    done: (wf) => ["question", "answer"].every((c) => wf.state.channels.some((ch) => ch.name === c)),
  },
  {
    id: "llm",
    title: "Add a step that thinks",
    concept: "Steps are the boxes. An LLM step fills a prompt template with state values and writes the model's reply into a channel.",
    todo: "Drag “LLM” from the palette onto the canvas; set its prompt to {{ state.question }} and its output to answer.",
    done: (wf) => nodesOf(wf, "core.llm").some((n) => n.config?.output === "answer" && String(n.config?.prompt ?? "").includes("state.question")),
  },
  {
    id: "flow",
    title: "Connect the flow",
    concept: "Flow edges decide what runs next. A run starts at START and finishes when it reaches END.",
    todo: "Drag from START's handle to your LLM step, then from the LLM step to END.",
    done: (wf) => nodesOf(wf, "core.llm").some((n) => flowTo(wf, n.id) && flowFrom(wf, n.id))
      && wf.edges.some((e) => e.kind === "flow" && e.source.node === "START")
      && wf.edges.some((e) => e.kind === "flow" && e.target.node === "END"),
  },
  {
    id: "model",
    title: "Plug in a model",
    concept: "Resources (models, tools, memory) are pills you wire into steps. The same model can serve several steps.",
    todo: "Drag “Chat model” onto the canvas and wire it into the LLM step's model port.",
    done: (wf) => {
      const models = new Set(wf.resources.filter((r) => r.type === "model.chat").map((r) => r.id));
      return wf.edges.some((e) => e.kind === "wiring" && models.has(e.source.node));
    },
  },
  {
    id: "run",
    title: "Run it",
    concept: "A run executes the graph on a thread. Every step is checkpointed, so you can inspect, edit and replay any point in the timeline.",
    todo: "Open the Run tab, enter {\"question\": \"What is a graph?\"} and press ▶ Run.",
    done: (_wf, run) => run.finished,
  },
  {
    id: "approval",
    title: "Put a human in the loop",
    concept: "An Approval step pauses the run (an interrupt) until someone decides in the Run tab or the Inbox; the run then resumes exactly where it stopped.",
    todo: "Add an “Approval” step between the LLM step and END showing answer, then run again and approve it.",
    done: (wf, run) => nodesOf(wf, "core.approval").length > 0 && run.interrupted,
  },
];

/** Index of the first step that is not done yet (STEPS.length when the tour is complete). */
export function currentStep(wf: WF, run: TutorRun): number {
  const i = STEPS.findIndex((s) => !s.done(wf, run));
  return i === -1 ? STEPS.length : i;
}
