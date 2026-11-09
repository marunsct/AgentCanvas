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
/*
 * Templates are Jinja as the Python engine reads it (sandboxed, StrictUndefined), on nunjucks. While a template
 * renders (synchronously), `jinja()` puts in place what nunjucks does differently: a missing field fails but a null
 * one prints `None`, booleans print `True`/`False`, `[]`, `{}` and `""` are false in `if`, `not`, `and`, `or`,
 * `| tojson` writes JSON as Jinja does, and nothing reaches `constructor`, `__proto__` or `prototype` (the way
 * out of the sandbox to run code). Lists and objects print as JSON (Python prints its own repr). Still different:
 * negative indexes and slices (`state.l[-1]`, `state.s[:2]`), Python string methods (`state.s.upper()`), `%`
 * formatting, and floats with nothing after the point (`2.0` prints `2`).
 */
const templates = new nunjucks.Environment(null, { autoescape: false, throwOnUndefined: true });
const conditions = new Map<string, (ctx: Record<string, any>) => any>();
const TRUTHY = "__ac_truthy__";
const BLOCKED = new Set(["constructor", "prototype", "__proto__", "__defineGetter__", "__defineSetter__",
  "__lookupGetter__", "__lookupSetter__"]);

/** Whether `value` is true as Python reads it: empty lists, objects and strings are false. */
export function truthy(value: any): boolean {
  if (value instanceof String) return value.length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (value !== null && typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

/** JSON as Jinja's `tojson` writes it: keys sorted, `", "` and `": "` between items, safe in HTML. */
export function tojson(value: any): string {
  const escape = (c: string) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`;
  const dump = (v: any): string => {
    if (v === null || v === undefined || typeof v === "function") return "null";
    if (Array.isArray(v)) return `[${v.map(dump).join(", ")}]`;
    if (typeof v === "object" && !(v instanceof String)) {
      return `{${Object.keys(v).sort().map((k) => `${dump(k)}: ${dump(v[k])}`).join(", ")}}`;
    }
    if (typeof v === "string" || v instanceof String) {
      return JSON.stringify(String(v)).replace(/[\u0080-\uffff]/g, escape);
    }
    return JSON.stringify(v) ?? "null";
  };
  return dump(value).replace(/[<>&']/g, escape);
}
templates.addFilter("tojson", (value: any) => new nunjucks.runtime.SafeString(tojson(value)));
templates.addGlobal(TRUTHY, truthy);

/** What a printed value reads as (Python's `None`, `True` and `False`; lists and objects as JSON). */
function printed(value: any): any {
  if (value === null) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (value instanceof String || value === undefined || typeof value !== "object") return value;
  return JSON.stringify(value);
}

function allowed(obj: any, key: any): void {
  if (typeof key !== "string" || !BLOCKED.has(key)) return;
  // a field of the state that happens to have such a name is data; anything else is the way out of the sandbox
  if (obj !== null && obj !== undefined && typeof obj === "object" && Object.hasOwn(obj, key)
    && typeof obj[key] !== "function") return;
  throw new Error(`access to '${key}' is not allowed in a template`);
}

const nodes: any = (nunjucks as any).nodes;
const runtime: any = nunjucks.runtime;
const compiler: any = (nunjucks as any).compiler.Compiler.prototype;
const asTruthy = (node: any) => new nodes.FunCall(node.lineno, node.colno, new nodes.Symbol(node.lineno, node.colno,
  TRUTHY), new nodes.NodeList(node.lineno, node.colno, [node]));
const patches: [any, string, (orig: any) => any][] = [
  [runtime, "ensureDefined", () => (value: any, lineno: number, colno: number) => {
    if (value === undefined) {
      throw new nunjucks.lib.TemplateError("attempted to output an undefined value", lineno + 1, colno + 1);
    }
    return value;
  }],
  [runtime, "suppressValue", (orig) => (value: any, autoescape: boolean) => orig(printed(value), autoescape)],
  [runtime, "memberLookup", (orig) => (obj: any, key: any) => {
    allowed(obj, key);
    return orig(obj, key);
  }],
  [runtime, "contextOrFrameLookup", (orig) => (context: any, frame: any, name: string) => {
    allowed(context.ctx, name);
    return orig(context, frame, name);
  }],
  [compiler, "compileIf", (orig) => function (this: any, node: any, frame: any, async: any) {
    node.cond = asTruthy(node.cond);
    return orig.call(this, node, frame, async);
  }],
  [compiler, "compileInlineIf", (orig) => function (this: any, node: any, frame: any) {
    node.cond = asTruthy(node.cond);
    return orig.call(this, node, frame);
  }],
  [compiler, "compileNot", (orig) => function (this: any, node: any, frame: any) {
    node.target = asTruthy(node.target);
    return orig.call(this, node, frame);
  }],
  [compiler, "compileOr", () => function (this: any, node: any, frame: any) {
    this.compileInlineIf(new nodes.InlineIf(node.lineno, node.colno, node.left, node.left, node.right), frame);
  }],
  [compiler, "compileAnd", () => function (this: any, node: any, frame: any) {
    this.compileInlineIf(new nodes.InlineIf(node.lineno, node.colno, node.left, node.right, node.left), frame);
  }],
];

/** `fn` with nunjucks reading templates as Jinja does (see above); put back afterwards. */
function jinja<T>(fn: () => T): T {
  const saved = patches.map(([obj, key]) => [obj, key, obj[key]] as const);
  for (const [obj, key, patch] of patches) obj[key] = patch(obj[key]);
  try {
    return fn();
  } finally {
    for (const [obj, key, orig] of saved) obj[key] = orig;
  }
}

/** A prompt or value with `{{ state.<field> }}` filled in (lists and objects are written as JSON). */
export function render(template: string, state: State, names: Record<string, any> = {}): string {
  try {
    return jinja(() => templates.renderString(template, { ...names, state: printable(copy(state)) }));
  } catch (exc) {
    throw new Error(`template error in '${template.slice(0, 40)}…': ${(exc as Error).message}`);
  }
}

// Lists and objects read as JSON where they become text (`~`, `join`); a field named `toString` stays a field.
class JsonList extends Array {
  toString(): string {
    return JSON.stringify(this);
  }
}
const JSON_OBJECT = Object.create(Object.prototype, { toString: { value(this: any) {
  return JSON.stringify(this);
} } });

function printable(value: any): any {
  if (value === null || typeof value !== "object") return value;
  for (const key of Object.keys(value)) value[key] = printable(value[key]);
  return Object.setPrototypeOf(value, Array.isArray(value) ? JsonList.prototype : JSON_OBJECT);
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
  decides?: string; // a Decide with AI step: the field its decision is saved in, which picks the path
  minConfidence?: number; // ... below which it takes `default`
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
    if (step.decides !== undefined) {
      return [...(nxt[chosen(values[step.decides], step.default ?? "default", step.minConfidence ?? null)] ?? [])];
    }
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
  next_round?: string[]; // steps the round's finished steps lead to
  arrived?: Record<string, string[]>; // "wait for all" step -> branches in since it ran
};

/** LangGraph's `add_messages` on JSON messages: a message whose `id` is already there replaces it, a
 * `{ type: "remove", id }` takes that message out (the id `__remove_all__`: every message), anything else is
 * appended. */
export function addMessages(old: any, value: any): any[] {
  const same = (o: any, id: any) => id !== undefined && id !== null && o && typeof o === "object" && o.id === id;
  let out: any[] = [...(old ?? [])];
  for (const m of Array.isArray(value) ? value : [value]) {
    const id = m && typeof m === "object" ? m.id : undefined;
    if (m && typeof m === "object" && m.type === "remove") {
      out = id === "__remove_all__" ? [] : out.filter((o) => !same(o, id));
      continue;
    }
    const at = out.findIndex((o) => same(o, id));
    if (at < 0) out.push(m);
    else out[at] = m;
  }
  return out;
}

export function reduce(reducer: string, old: any, value: any): any {
  if (reducer === "add_messages") return addMessages(old, value);
  if (reducer === "append") {
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
  /** Aborted when the run is cancelled (model calls stop with it). */
  signal?: AbortSignal;
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
  let values: State, frontier: string[], done: Set<string>, steps: number, rounds: Rounds;
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
      rounds = new Rounds([...paused.next_round ?? []], Object.fromEntries(Object.entries(paused.arrived ?? {})
        .map(([k, v]) => [k, new Set(v)])), true);
    } else {
      values = Object.fromEntries(Object.entries(flow.channels).map(([k, [, initial]]) => [k, copy(initial)]));
      Object.assign(values, copy(opts.input ?? {}));
      rounds = new Rounds([], {});
      frontier = rounds.lead(flow, "START", values).filter((n) => n !== "END");
      done = new Set();
      steps = 0;
    }
    yield* loop(flow, host, runClass, values, frontier, done, steps, answers, native, opts.runId ?? "run",
      rounds);
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
  runId: string, rounds: Rounds): AsyncGenerator<Event> {
  const limit = flow.recursionLimit ?? 25;
  while ((frontier = rounds.ready(flow, frontier)).length) {
    steps += 1;
    if (steps > limit) {
      yield { type: "RUN_ERROR", code: "recursion_limit", message: `stopped after ${limit} steps without reaching END` };
      return;
    }
    const next = rounds.following;
    rounds.following = [];
    for (const [i, nodeId] of frontier.entries()) {
      const step = flow.steps[nodeId];
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
            frontier: [nodeId, ...frontier.slice(i + 1)], done: [...done].sort(), payload: exc.payload,
            interrupt_id: info.id, steps: steps - 1, native: exc.native, next_round: next,
            arrived: Object.fromEntries(Object.entries(rounds.arrived).filter(([, v]) => v.size)
              .map(([k, v]) => [k, [...v].sort()])) };
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
      for (const n of rounds.lead(flow, nodeId, values)) if (!next.includes(n)) next.push(n);
    }
    frontier = next.filter((n) => n !== "END");
  }
  yield { type: "RUN_FINISHED", outcome: "success", breakpoint: false, interrupts: [], next: [], checkpoint_id: null,
    output: outputs(flow, values) };
}

/**
 * Which steps run next. A "wait for all" step runs once every branch into it has arrived since it last ran (a loop
 * waits again on each pass); one still waiting when nothing else is left to run is dropped, as when a path skipped
 * one of its branches.
 */
class Rounds {
  following: string[]; // the next round, already started (a resumed run)
  arrived: Record<string, Set<string>>;
  resumed: boolean;

  constructor(following: string[], arrived: Record<string, Set<string>>, resumed = false) {
    this.following = following;
    this.arrived = arrived;
    this.resumed = resumed;
  }

  lead(flow: Flow, nodeId: string, values: State): string[] {
    const nxt = following(flow, nodeId, values);
    for (const target of nxt) {
      if (flow.steps[target]?.type === "core.join") (this.arrived[target] ??= new Set()).add(nodeId);
    }
    return nxt;
  }

  ready(flow: Flow, frontier: string[]): string[] {
    if (this.resumed) { // the paused round goes on as it was
      this.resumed = false;
      return frontier;
    }
    return frontier.filter((nodeId) => {
      if (flow.steps[nodeId].type !== "core.join") return true;
      const arrived = this.arrived[nodeId] ?? new Set();
      if (![...before(flow, nodeId)].every((n) => arrived.has(n))) return false;
      this.arrived[nodeId] = new Set();
      return true;
    });
  }
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
// `addUsedSchema: false`: a schema with an `$id` is not kept, so asking again with it (or another one with the same
// `$id`) works; validators are kept by schema instead
const ajv = new Ajv2020({ allErrors: true, strict: false, addUsedSchema: false });
const validators = new Map<string, ReturnType<typeof ajv.compile>>();

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
      const key = JSON.stringify(opts.schema);
      let validate = validators.get(key);
      if (validate === undefined) validators.set(key, validate = ajv.compile(opts.schema));
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

  /** What a Decide with AI step asks its model: the rendered question, the options and the answer's shape. */
  decidePrompt(prompt: string, choices: Record<string, string>): string {
    return decidePrompt(prompt, choices);
  }

  /** The model's decision, saved in `output`; FLOW follows its choice (see `Step.decides`). */
  decision(reply: string, choices: Record<string, string>, opts: { output: string }): Update {
    return { [opts.output]: aiDecision(reply, choices) };
  }

  limitNote(agent: string, limit: number, opts: { output: string }): Update {
    const note = `Stopped: ${agent} reached its limit of ${limit} model calls before finishing.`;
    return { [opts.output]: [{ type: "ai", role: "assistant", content: note, name: agent }] };
  }
}

// ------------------------------------------------------------- Decide with AI ----
// The twin of `agentcanvas/expr.py`: the same words to the model and the same reading of its answer.
export const DECIDE_INSTRUCTIONS =
  "Choose exactly one of these options:\n{options}\n\nAnswer with JSON only, no other text: " +
  '{"choice": "<one option name>", "confidence": <0 to 1, how sure you are>, "reason": "<one short sentence>"}';

export function decidePrompt(prompt: string, choices: Record<string, string>): string {
  const options = Object.entries(choices).map(([route, when]) => `- ${route}: ${when}`).join("\n");
  return `${prompt}\n\n` + DECIDE_INSTRUCTIONS.replace("{options}", options);
}

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
const MAX_OBJECTS = 100; // `{` the answer is read from at most, as in Python

/** Where the JSON value that starts with `{` at `start` ends (its matching `}`, strings skipped), or -1. */
function objectEnd(text: string, start: number): number {
  let depth = 0;
  let inString = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (c === "\\") i++;
      else if (c === '"') inString = false;
    } else if (c === '"') inString = true;
    else if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") {
      depth--;
      if (depth === 0) return c === "}" ? i : -1;
    }
  }
  return -1;
}

/** The JSON object in a model's answer that holds a `choice` (else the first object): read at each `{` in turn, as
 * Python's raw_decode does, so reasoning, a code fence or braces around the JSON do not hide it. */
function decisionObject(text: string): Record<string, any> {
  let first: Record<string, any> | null = null;
  let start = text.indexOf("{");
  for (let n = 0; n < MAX_OBJECTS && start >= 0; n++) {
    const end = objectEnd(text, start);
    let found: any = null;
    if (end > start) {
      try {
        found = JSON.parse(text.slice(start, end + 1));
      } catch {
        found = null;
      }
    }
    if (isObject(found)) {
      if (Object.hasOwn(found, "choice")) return found;
      if (first === null) first = found;
    }
    start = text.indexOf("{", start + 1);
  }
  return first ?? {};
}

/** The decision in a model's answer: {choice, confidence, reason}; `choice` is null when the answer names no
 * option (no JSON, or a name that is none of `choices`, in any case). */
export function aiDecision(text: string, choices: Record<string, string> | string[]): Record<string, any> {
  const found = decisionObject(text);
  const names = Array.isArray(choices) ? choices : Object.keys(choices);
  const raw = found.choice;
  const choice = typeof raw === "string" ? raw.trim() : "";
  const folded = new Map<string, string>();
  for (const n of names) if (!folded.has(n.toLowerCase())) folded.set(n.toLowerCase(), n);
  const name = !choice ? null : names.includes(choice) ? choice : (folded.get(choice.toLowerCase()) ?? null);
  const given = found.confidence;
  let value = Number.NaN;
  if (typeof given === "number") value = given;
  else if (typeof given === "boolean") value = given ? 1 : 0;
  else if (typeof given === "string" && NUMBER.test(given.trim())) value = Number(given.trim());
  const reason = found.reason;
  return {
    choice: name,
    confidence: Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : null,
    reason: typeof reason === "string" ? reason.slice(0, 500) : "",
  };
}

/** The path a decision takes: its choice, or `fallback` when it made none or is less sure than the minimum. */
export function chosen(made: any, fallback: string, minConfidence: number | null): string {
  const m = isObject(made) ? made : {};
  const confidence = m.confidence;
  const unsure = minConfidence !== null && (typeof confidence !== "number" || confidence < minConfidence);
  return unsure || !m.choice ? fallback : String(m.choice);
}

// ------------------------------------------------------------------ helpers ----
function isObject(value: any): value is Record<string, any> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * A person's answer to tool approvals as one decision per call, each with its `type` (a bare yes/no applies to all;
 * a call the answer leaves out is rejected).
 */
export function decisions(answer: any, count: number): Record<string, any>[] {
  if (isObject(answer) && Array.isArray(answer.decisions)) {
    const found = answer.decisions.map(decision);
    return [...found, ...Array.from({ length: count }, () => ({ type: "reject" }))].slice(0, count);
  }
  return Array.from({ length: count }, () => ({ type: yes(answer) ? "approve" : "reject" }));
}

function yes(answer: any): boolean {
  if (isObject(answer)) answer = answer.approved;
  if (typeof answer === "string") return YES_WORDS.has(answer.trim().toLowerCase()); // "no" is not a yes
  return (typeof answer === "boolean" || typeof answer === "number") && Boolean(answer);
}

function decision(given: any): Record<string, any> {
  if (isObject(given) && ["approve", "edit", "reject"].includes(given.type)) return given;
  if (typeof given === "string" && ["edit", "reject"].includes(given.trim().toLowerCase())) {
    return { type: given.trim().toLowerCase() };
  }
  return { ...(isObject(given) ? given : {}), type: yes(given) ? "approve" : "reject" };
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
