/**
 * The flow engine for Mastra flows (S36): `supersteps.py` in TypeScript, so a workflow designed for Mastra runs on
 * Node.js exactly as the canvas draws it.
 *
 * A workflow becomes plain TypeScript (see `codegen.py`): one async function per block and a `FLOW` that names the
 * steps and what follows each. This module runs that flow in supersteps: steps side by side run in the same round, a
 * "wait for all" step waits for every branch, paths follow the first CEL rule that matches, and fields merge with
 * their reducers. A step that waits for a person ends the run with a `Paused` value (JSON) that continues it later
 * from that step, with the step's own Mastra state (its suspended agent run) kept in `native`.
 *
 * `runtime.ts` subclasses `Run` with the Mastra parts (models, tools, agents). The same module runs inside
 * AgentCanvas (in a Node.js worker) and in exports. Node.js 22.18+ runs it as it is (types are stripped).
 */

import { parse as parseCel } from "@marcbachmann/cel-js";
import { Ajv2020 } from "ajv/dist/2020.js";
import nunjucks from "nunjucks";

export const YES_WORDS = new Set(["yes", "y", "true", "approve", "approved", "ok", "okay"]);
const ROLES: Record<string, string> = { human: "user", user: "user", ai: "assistant", assistant: "assistant",
  system: "system" };
export const MISSING: unique symbol = Symbol("missing");

export type State = Record<string, any>;
export type Update = Record<string, any>;
export type Event = Record<string, any>;

// ------------------------------------------------------------------ expressions ----
const templates = new nunjucks.Environment(null, { autoescape: false, throwOnUndefined: true });
const conditions = new Map<string, (ctx: Record<string, any>) => any>();

/** A prompt or value with `{{ state.<field> }}` filled in (lists and objects are written as JSON). */
export function render(template: string, state: State, names: Record<string, any> = {}): string {
  try {
    return templates.renderString(template, { ...names, state: printable(copy(state)) });
  } catch (exc) {
    throw new Error(`template error in '${template.slice(0, 40)}…': ${(exc as Error).message}`);
  }
}

function printable(value: any): any {
  if (value === null || typeof value !== "object") return value;
  for (const key of Object.keys(value)) value[key] = printable(value[key]);
  Object.defineProperty(value, "toString", { value: () => JSON.stringify(value), enumerable: false });
  return value;
}

/** Whether the CEL rule holds for `state` (whole numbers are CEL ints, as in the Python engine). */
export function condition(source: string, state: State): boolean {
  let program = conditions.get(source);
  if (program === undefined) {
    try {
      program = parseCel(source) as unknown as (ctx: Record<string, any>) => any;
    } catch (exc) {
      throw new Error(`invalid condition '${source}': ${(exc as Error).message.split("\n")[0]}`);
    }
    conditions.set(source, program);
  }
  try {
    return Boolean(program({ state: celValue(copy(state)) }));
  } catch (exc) {
    throw new Error(`cannot evaluate '${source}': ${(exc as Error).message.split("\n")[0]}`);
  }
}

function celValue(value: any): any {
  if (Array.isArray(value)) return value.map(celValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, celValue(v)]));
  }
  return Number.isInteger(value) ? BigInt(value) : value;
}

// ------------------------------------------------------------------ the flow ----
/** A step waits for a person. `native` is what the step needs to continue (its Mastra run, saved). */
export class Interrupted extends Error {
  payload: Record<string, any>;
  native: string | null;

  constructor(payload: Record<string, any>, native: string | null = null) {
    super("interrupted");
    this.payload = payload;
    this.native = native;
  }
}

/** A step ends the run on purpose (a model call limit set to fail). */
export class StopRun extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export type StepFn = (state: State, run: any) => Promise<Update> | Update;

export type Step = {
  name: string;
  type: string;
  run?: StepFn; // none: a path (router) or "wait for all" (join), which change nothing
  cases?: [string, string][]; // router: [CEL rule, route], the first match wins
  default?: string;
  disabled?: boolean;
};

export type Flow = {
  name: string;
  steps: Record<string, Step>; // canvas node id -> step
  start: string[];
  next: Record<string, string[] | Record<string, string[]>>; // node id -> following ids (a router: per route)
  channels: Record<string, [string, any]>; // field -> [reducer, default]
  outputs: string[];
  recursionLimit?: number;
};

export function following(flow: Flow, nodeId: string, values: State): string[] {
  const nxt = nodeId === "START" ? flow.start : flow.next[nodeId] ?? [];
  if (!Array.isArray(nxt)) {
    const step = flow.steps[nodeId];
    const hit = (step.cases ?? []).find(([when]) => condition(when, values));
    return [...(nxt[hit ? hit[1] : step.default ?? "default"] ?? [])];
  }
  return [...nxt];
}

export function before(flow: Flow, nodeId: string): Set<string> {
  const out = new Set<string>();
  for (const [src, nxt] of Object.entries(flow.next)) {
    const targets = Array.isArray(nxt) ? [nxt] : Object.values(nxt);
    if (targets.some((ids) => ids.includes(nodeId))) out.add(src);
  }
  if (flow.start.includes(nodeId)) out.add("START");
  return out;
}

/** A run waiting for a person: where it stopped and what it had done (JSON). */
export type Paused = {
  values: State;
  node_id: string; // the step that asked
  frontier: string[]; // steps still to run in that round (the asking one first)
  done: string[];
  payload: Record<string, any>;
  interrupt_id: string;
  steps: number;
  native: string | null; // the asking step's own Mastra state
};

export function reduce(reducer: string, old: any, value: any): any {
  if (reducer === "append" || reducer === "add_messages") {
    return [...(old ?? []), ...(Array.isArray(value) ? value : [value])];
  }
  if (reducer === "sum") return (old ?? 0) + (value ?? 0);
  if (reducer === "merge") return { ...(old ?? {}), ...(value ?? {}) };
  return value;
}

export function interruptKind(payload: Record<string, any>): string {
  if ("action_requests" in payload) return "tool_approval";
  return payload.kind === "approval" ? "approval" : "input";
}

// ------------------------------------------------------------------ running ----
export type Host = {
  /** The model for the canvas model block `resource`, used by the step named `step`. */
  model(resource: string, step: string, settings: Record<string, any>): any;
  /** The canvas tool blocks `resources` as tool specs that run each call. */
  tools(resources: string[], step: string): ToolSpec[];
  search(knowledgeBase: string, query: string, opts: { mode: string; top_k: number; min_score: number }):
    Promise<Record<string, any>[]>;
  script(importPath: string, state: State): Promise<Update>;
};

export type ToolSpec = {
  name: string;
  description: string;
  parameters: Record<string, any>; // JSON schema of the arguments
  call: (args: Record<string, any>) => Promise<string>;
};

export type RunClass = new (flow: Flow, host: Host, nodeId: string, name: string, answer: any,
  native: string | null) => Run;

/**
 * Run `flow` from the start with `input`, or continue a paused run with a person's answer; each step gets a
 * `runClass`. Yields AG-UI style events; the last one is RUN_FINISHED (outcome success or interrupt; a pause carries
 * `paused`) or RUN_ERROR.
 */
export async function* run(flow: Flow, host: Host, runClass: RunClass, opts: { input?: State | null;
  resume?: [Paused, any] | null; runId?: string } = {}): AsyncGenerator<Event> {
  let values: State, frontier: string[], done: Set<string>, steps: number;
  const answers = new Map<string, any>();
  const native = new Map<string, string>();
  try {
    if (opts.resume) {
      const [paused, answer] = opts.resume;
      values = copy(paused.values);
      frontier = [...paused.frontier];
      done = new Set(paused.done);
      steps = paused.steps;
      answers.set(paused.node_id, answer);
      if (paused.native) native.set(paused.node_id, paused.native);
    } else {
      values = Object.fromEntries(Object.entries(flow.channels).map(([k, [, initial]]) => [k, copy(initial)]));
      Object.assign(values, copy(opts.input ?? {}));
      frontier = following(flow, "START", values).filter((n) => n !== "END");
      done = new Set();
      steps = 0;
    }
    yield* loop(flow, host, runClass, values, frontier, done, steps, answers, native, opts.runId ?? "run");
  } catch (exc) {
    if (exc instanceof StopRun) {
      yield { type: "RUN_ERROR", code: exc.code, message: exc.message };
    } else {
      const err = exc as Error;
      yield { type: "RUN_ERROR", code: "error", message: `${err?.name ?? "Error"}: ${err?.message ?? String(exc)}` };
    }
  }
}

async function* loop(flow: Flow, host: Host, runClass: RunClass, values: State, frontier: string[],
  done: Set<string>, steps: number, answers: Map<string, any>, native: Map<string, string>,
  runId: string): AsyncGenerator<Event> {
  const limit = flow.recursionLimit ?? 25;
  while (frontier.length) {
    steps += 1;
    if (steps > limit) {
      yield { type: "RUN_ERROR", code: "recursion_limit", message: `stopped after ${limit} steps without reaching END` };
      return;
    }
    const next: string[] = [];
    for (const [i, nodeId] of frontier.entries()) {
      const step = flow.steps[nodeId];
      if (step.type === "core.join") {
        const ready = new Set([...done, ...frontier.slice(0, i), "START"]);
        if (![...before(flow, nodeId)].every((n) => ready.has(n))) {
          next.push(nodeId); // wait for the other branches
          continue;
        }
      }
      yield { type: "STEP_STARTED", stepName: step.name, canvas_node_id: nodeId, task_id: nodeId };
      const started = performance.now();
      const ctx = new runClass(flow, host, nodeId, step.name, answers.has(nodeId) ? answers.get(nodeId) : MISSING,
        native.get(nodeId) ?? null);
      let update: Update;
      try {
        update = step.disabled || !step.run ? {} : (await step.run(values, ctx)) ?? {};
      } catch (exc) {
        if (exc instanceof Interrupted) {
          const info = { id: `${runId}:${nodeId}`, kind: interruptKind(exc.payload), canvas_node_id: nodeId,
            payload: exc.payload };
          const paused: Paused = { values: copy(values), node_id: nodeId,
            frontier: [nodeId, ...frontier.slice(i + 1), ...next], done: [...done].sort(), payload: exc.payload,
            interrupt_id: info.id, steps: steps - 1, native: exc.native };
          yield { type: "STEP_FINISHED", stepName: step.name, canvas_node_id: nodeId, status: "interrupted",
            task_id: nodeId, duration_ms: ms(started) };
          yield { type: "CUSTOM", name: "ac.interrupt", value: info };
          yield { type: "RUN_FINISHED", outcome: "interrupt", breakpoint: false, interrupts: [info], next: [nodeId],
            checkpoint_id: null, output: outputs(flow, values), paused };
          return;
        }
        yield { type: "STEP_FINISHED", stepName: step.name, canvas_node_id: nodeId, status: "error", task_id: nodeId,
          duration_ms: ms(started) };
        throw exc;
      }
      answers.delete(nodeId);
      native.delete(nodeId);
      for (const [k, v] of Object.entries(update)) {
        values[k] = reduce(flow.channels[k]?.[0] ?? "replace", values[k], v);
      }
      done.add(nodeId);
      yield { type: "STEP_FINISHED", stepName: step.name, canvas_node_id: nodeId, status: "ok", task_id: nodeId,
        duration_ms: ms(started) };
      if (Object.keys(update).length) yield { type: "STATE_DELTA", canvas_node_id: nodeId, delta: copy(update) };
      for (const n of following(flow, nodeId, values)) if (!next.includes(n)) next.push(n);
    }
    frontier = next.filter((n) => n !== "END");
  }
  yield { type: "RUN_FINISHED", outcome: "success", breakpoint: false, interrupts: [], next: [], checkpoint_id: null,
    output: outputs(flow, values) };
}

export function outputs(flow: Flow, values: State): State {
  return Object.fromEntries(flow.outputs.filter((k) => k in values).map((k) => [k, copy(values[k])]));
}

function ms(started: number): number {
  return Math.round(performance.now() - started);
}

export function copy<T>(value: T): T {
  return value !== null && typeof value === "object" ? JSON.parse(JSON.stringify(value)) : value;
}

// ------------------------------------------------------------------ steps ----
const ajv = new Ajv2020({ allErrors: true, strict: false });

/**
 * What one step of a flow can do: ask a person, search, run workspace code. `runtime.ts` adds the Mastra models,
 * tools and agents.
 */
export class Run {
  flow: Flow;
  host: Host;
  nodeId: string;
  name: string;
  answer: any;
  native: string | null;

  constructor(flow: Flow, host: Host, nodeId: string, name: string, answer: any = MISSING,
    native: string | null = null) {
    this.flow = flow;
    this.host = host;
    this.nodeId = nodeId;
    this.name = name;
    this.answer = answer;
    this.native = native;
  }

  /** The pause for tool calls that wait for a person's approval (name and arguments of each). */
  toolRequest(calls: [string, Record<string, any>][], native: string): Interrupted {
    const requests = calls.map(([n, a]) => ({ name: n, args: a, description: `${this.name} wants to call ${n}` }));
    const review = calls.map(([n]) => ({ action_name: n, allowed_decisions: ["approve", "edit", "reject"] }));
    return new Interrupted({ action_requests: requests, review_configs: review, canvas_node_id: this.nodeId }, native);
  }

  /** A person's decisions on the paused tool calls (null: this step is not continuing a tool approval). */
  toolAnswers(count: number): Record<string, any>[] | null {
    if (this.native === null || this.answer === MISSING) return null;
    return decisions(this.answer, count);
  }

  // ---- people ----
  /** Pause until a person approves or rejects `show` (and maybe edits it). */
  approval(state: State, opts: { show: string; output: string }): Update {
    if (this.answer === MISSING) {
      throw new Interrupted({ kind: "approval", canvas_node_id: this.nodeId, value: state[opts.show] ?? null });
    }
    const decision = isObject(this.answer) ? this.answer : { approved: this.answer };
    let approved = decision.approved;
    if (typeof approved === "string") approved = YES_WORDS.has(approved.trim().toLowerCase()); // "no" is not a yes
    const out: Update = { [opts.output]: Boolean(approved) };
    if (decision.value !== undefined && decision.value !== null) out[opts.show] = decision.value;
    return out;
  }

  /** Pause until a person answers `prompt`; an answer that does not fit `schema` is asked again. */
  ask(state: State, opts: { prompt: string; output: string; schema?: Record<string, any> }): Update {
    const request: Record<string, any> = { kind: "input", canvas_node_id: this.nodeId,
      prompt: render(opts.prompt, state) };
    if (opts.schema) request.response_schema = opts.schema;
    if (this.answer === MISSING) throw new Interrupted(request);
    if (opts.schema) {
      const validate = ajv.compile(opts.schema);
      if (!validate(this.answer)) {
        const first = (validate.errors ?? []).map((e) => `${e.instancePath || "answer"} ${e.message}`).sort()[0];
        throw new Interrupted({ ...request, error: first });
      }
    }
    return { [opts.output]: this.answer };
  }

  // ---- knowledge and code ----
  async search(knowledgeBase: string, query: string, opts: { output: string; mode?: string; top_k?: number;
    min_score?: number; sources_output?: string }): Promise<Update> {
    const found = await this.host.search(knowledgeBase, query, { mode: opts.mode ?? "hybrid",
      top_k: opts.top_k ?? 5, min_score: opts.min_score ?? 0 });
    const out: Update = { [opts.output]: found };
    if (opts.sources_output) {
      out[opts.sources_output] = found.map((r) => ({ cite: r.cite ?? null, title: r.title ?? null,
        source: r.source ?? null, page: r.page ?? null }));
    }
    return out;
  }

  async script(importPath: string, state: State): Promise<Update> {
    return { ...((await this.host.script(importPath, copy(state))) ?? {}) };
  }

  // ---- answers ----
  /** An agent's answer as the fields it sets: a message appended to `output`, and with a typed answer also
   * `structured_response`. */
  reply(agent: string, answer: any, opts: { output: string; structured: boolean }): Update {
    if (opts.structured && answer !== null && answer !== undefined && typeof answer !== "string") {
      return { [opts.output]: [{ type: "ai", role: "assistant", content: JSON.stringify(answer), name: agent }],
        structured_response: copy(answer) };
    }
    const text = typeof answer === "string" ? answer : answer === null || answer === undefined ? ""
      : JSON.stringify(answer);
    return { [opts.output]: [{ type: "ai", role: "assistant", content: text, name: agent }] };
  }

  limitNote(agent: string, limit: number, opts: { output: string }): Update {
    const note = `Stopped: ${agent} reached its limit of ${limit} model calls before finishing.`;
    return { [opts.output]: [{ type: "ai", role: "assistant", content: note, name: agent }] };
  }
}

// ------------------------------------------------------------------ helpers ----
function isObject(value: any): value is Record<string, any> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** A person's answer to tool approvals as one decision per call (a bare yes/no applies to all). */
export function decisions(answer: any, count: number): Record<string, any>[] {
  if (isObject(answer) && Array.isArray(answer.decisions)) {
    const found = answer.decisions.map((d: any) => (isObject(d) ? d : { type: d ? "approve" : "reject" }));
    const last = found.length ? found[found.length - 1] : { type: "reject" };
    return [...found, ...Array(count).fill(last)].slice(0, count);
  }
  if (isObject(answer)) answer = answer.approved;
  if (typeof answer === "string") answer = YES_WORDS.has(answer.trim().toLowerCase());
  return Array(count).fill({ type: answer ? "approve" : "reject" });
}

/** The arguments a person wrote for an edited call (null: not an edit). */
export function editedArgs(decision: Record<string, any>): Record<string, any> | null {
  if (decision.type !== "edit") return null;
  return { ...(decision.edited_action?.args ?? {}) };
}

/** What an agent is asked: the latest message, after the earlier turns of the conversation if any. */
export function conversation(messages: any): string {
  if (typeof messages === "string") return messages;
  const turns: [string, string][] = [];
  for (const given of messages ?? []) {
    if (typeof given === "string") {
      turns.push(["user", given]);
      continue;
    }
    const role = ROLES[String(given?.role ?? given?.type ?? "user")];
    let content = given?.content;
    if ((role !== "user" && role !== "assistant") || content === null || content === undefined || content === "") {
      continue;
    }
    if (typeof content !== "string") {
      content = content.map((b: any) => (isObject(b) ? b.text ?? "" : String(b))).join("");
    }
    turns.push([role, content]);
  }
  if (!turns.length) return "Go ahead.";
  if (turns.length === 1) return turns[0][1];
  const earlier = turns.slice(0, -1).map(([role, text]) => `${role}: ${text}`).join("\n");
  return `The conversation so far:\n${earlier}\n\nThe latest message:\n${turns[turns.length - 1][1]}`;
}
