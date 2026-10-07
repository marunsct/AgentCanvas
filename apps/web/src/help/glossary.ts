/** Plain-language help for the canvas: what each kind of connection carries, and what a port is for. Pure. */
import type { NodeManifest, PortSpec } from "../api/types";

export interface TypeInfo {
  name: string; // what a beginner calls it
  explain: string; // one sentence, no jargon
}

/** Port types (the colours on the canvas) in everyday words. */
export const PORT_TYPES: Record<string, TypeInfo> = {
  Flow: { name: "next step", explain: "Decides what happens next. Drag it to the step that should run after this one, or to Finish." },
  ChatModel: { name: "AI model", explain: "The AI “brain” (Claude, GPT, Gemini, a local model…) that does the thinking and writing." },
  Tool: { name: "tool", explain: "Something an agent can use on its own, like calling a web service or running your Python function." },
  Middleware: { name: "add-on", explain: "Changes how an agent behaves: limits, retries, safety checks, a to-do list." },
  Backend: { name: "file storage", explain: "Where a Planning Agent keeps its notes and files." },
  Subagent: { name: "helper agent", explain: "A specialist agent a Planning Agent can hand part of the work to." },
  Schema: { name: "data shape", explain: "Describes the fields a piece of data has." },
  Store: { name: "storage", explain: "Somewhere data is kept between runs." },
  Retriever: { name: "document search", explain: "Finds the documents that match a question." },
  Embeddings: { name: "embeddings model", explain: "Turns text into numbers so similar texts can be found." },
};

export function typeName(type: string): string {
  return PORT_TYPES[type]?.name ?? type;
}

/** Palette order: what a beginner reaches for first. Categories not listed (custom widgets) follow, A–Z. */
export const CATEGORY_ORDER = ["AI", "AI models", "Tools", "People", "Flow control", "Data", "Memory", "Agent add-ons"];

export function categoryRank(category: string): number {
  const i = CATEGORY_ORDER.indexOf(category);
  return i === -1 ? CATEGORY_ORDER.length : i;
}

/** The first sentence of a description (palette cards, menus). */
export function firstSentence(text: string | null | undefined): string {
  const t = (text ?? "").trim();
  const m = /^(.+?[.!?])(\s|$)/.exec(t);
  return m ? m[1] : t;
}

export interface PortHelp {
  title: string; // "model · AI model (required)"
  body: string; // what it is for, in plain words
  action: string; // what to do with it
}

/** Hover text for one port of a block. `side` is the side of the block the port is on. */
export function portHelp(m: NodeManifest | undefined, port: PortSpec | undefined, side: "in" | "out", handle?: string): PortHelp {
  if (!port) {
    // flow handles without a manifest port: the step's "in", a router's routes, the default "next"
    if (side === "in") {
      return {
        title: "Start this step",
        body: "Connect the previous step (or Start) here. This step runs when the previous one finishes.",
        action: "Drag from another step's right-hand dot to here.",
      };
    }
    const route = handle?.startsWith("route:") ? handle.slice(6) : null;
    return {
      title: route ? `Path “${route}”` : "Next step",
      body: route
        ? `Taken when the rule for “${route}” matches${route === "default" ? " (or when no rule matches)" : ""}.`
        : PORT_TYPES.Flow.explain,
      action: "Click to see what can come next, or drag to a step.",
    };
  }
  const info = PORT_TYPES[port.type];
  const many = port.multi ? " You can connect several." : "";
  const need = side === "in" ? (port.required ? " (required)" : " (optional)") : "";
  const body = port.description || info?.explain || "";
  if (side === "in") {
    return {
      title: `${port.name} · ${typeName(port.type)}${need}`,
      body: body + many,
      action: port.type === "Flow" ? "Connect the previous step here." : `Click to see which blocks can plug in here.`,
    };
  }
  return {
    title: port.type === "Flow" ? (port.name === "next" ? "Next step" : `Output “${port.name}”`) : `${port.name} · ${typeName(port.type)}`,
    body,
    action: port.type === "Flow" ? "Click to see what can come next, or drag to a step." : `Click to see where this can plug in.`,
  };
}

/** One-line hover text (native title) for places that can only show plain text. */
export function portTitle(h: PortHelp): string {
  return `${h.title}\n${h.body}\n${h.action}`;
}

/** "a tool", "an AI model", "an add-on". */
export function aType(type: string): string {
  const n = typeName(type);
  return `${/^[aeiou]/i.test(n) ? "an" : "a"} ${n}`;
}
