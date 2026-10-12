/**
 * The Mastra worker (S36): runs Mastra flows for an AgentCanvas server, in Node.js.
 *
 * Mastra is a TypeScript framework, so its flows run in a Node.js process next to the server, started from the
 * Mastra environment (`make mastra`, or AGENTCANVAS_MASTRA_DIR) exactly as the Python frameworks' workers are, and
 * speaking the same JSON lines (see `flowworker.py`): the server's messages on stdin, the worker's on stdout.
 *
 * Server -> worker: `{"op": "run", "run": id, "source": <generated module>, "input"|"resume": ..., "models": {...},
 * "tools": {...}}`, `{"op": "cancel", "run": id}` and `{"reply": n, "value"|"error": ...}`.
 * Worker -> server: `{"run": id, "event": {...}}`, `{"run": id, "call": n, "op": "tool"|"search"|"script", "args":
 * {...}}` (the server answers with a reply) and `{"run": id, "end": true}`.
 *
 * The canvas models are served by the server's model bridge (an OpenAI-compatible address with a key per step), so
 * every call is traced and costed there; tools, knowledge bases and workspace code stay on the server too.
 */

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, writeSync } from "node:fs";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

import type { Flow, Host, ToolSpec } from "./flow.ts";
import * as runtime from "./runtime.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const send = (msg: Record<string, any>) => writeSync(1, `${JSON.stringify(msg)}\n`);
// anything else printed goes to stderr: stdout carries the protocol only
process.stdout.write = process.stderr.write.bind(process.stderr) as typeof process.stdout.write;
console.log = console.info = console.debug = console.error;

/** A canvas model through the server's model bridge, with `temperature` and `maxTokens` sent on each call. */
export function model(baseURL: string, apiKey: string, settings: Record<string, any> = {}): any {
  return createOpenAICompatible({ name: "canvas", baseURL, apiKey, supportsStructuredOutputs: true,
    fetch: withSettings(settings) }).chatModel("canvas");
}

/** A `fetch` that adds a model block's settings to each chat request (the OpenAI names). */
export function withSettings(settings: Record<string, any>, base: typeof fetch = fetch): typeof fetch {
  const extra: Record<string, any> = {};
  if (settings.temperature !== undefined && settings.temperature !== null) extra.temperature = settings.temperature;
  if (settings.maxTokens !== undefined && settings.maxTokens !== null) extra.max_tokens = settings.maxTokens;
  if (!Object.keys(extra).length) return base;
  return (async (url: any, init?: any) => {
    if (init && typeof init.body === "string") init = { ...init, body: JSON.stringify({ ...JSON.parse(init.body),
      ...extra }) };
    return base(url, init);
  }) as typeof fetch;
}

class Worker {
  calls = new Map<number, { resolve: (v: any) => void; reject: (e: Error) => void }>();
  next = 0;
  runs = new Map<string, AbortController>();
  flows = new Map<string, Flow>();

  call(runId: string, op: string, args: Record<string, any>): Promise<any> {
    this.next += 1;
    const n = this.next;
    return new Promise((resolve, reject) => {
      this.calls.set(n, { resolve, reject });
      send({ run: runId, call: n, op, args });
    });
  }

  reply(msg: Record<string, any>): void {
    const waiting = this.calls.get(msg.reply);
    if (!waiting) return;
    this.calls.delete(msg.reply);
    if ("error" in msg) waiting.reject(new Error(String(msg.error)));
    else waiting.resolve(msg.value ?? null);
  }

  async flow(source: string): Promise<Flow> {
    const key = createHash("sha256").update(source).digest("hex").slice(0, 16);
    let found = this.flows.get(key);
    if (found === undefined) {
      const file = join(HERE, `workflow-${key}.ts`); // next to runtime.ts, which it imports
      writeFileSync(file, source);
      found = (await import(pathToFileURL(file).href)).FLOW as Flow;
      this.flows.set(key, found);
    }
    return found;
  }

  async run(msg: Record<string, any>): Promise<void> {
    const runId: string = msg.run;
    const cancel = new AbortController();
    this.runs.set(runId, cancel);
    try {
      const flow = await this.flow(msg.source);
      const host = new RemoteHost(this, runId, msg.models ?? {}, msg.tools ?? {});
      const resume = msg.resume ? [JSON.parse(msg.resume.paused), msg.resume.answer] as [any, any] : null;
      for await (const ev of runtime.run(flow, host, { input: msg.input, resume, runId: msg.thread ?? runId })) {
        if (cancel.signal.aborted) return;
        if (ev.paused !== undefined) ev.paused = JSON.stringify(ev.paused);
        send({ run: runId, event: ev });
      }
    } catch (exc) {
      const err = exc as Error;
      send({ run: runId, event: { type: "RUN_ERROR", code: "error", message: `${err.name}: ${err.message}` } });
    } finally {
      this.runs.delete(runId);
      send({ run: runId, end: true });
    }
  }
}

/** A run's models, tools, knowledge bases and code, all on the server. */
class RemoteHost implements Host {
  worker: Worker;
  runId: string;
  models: Record<string, { base_url: string; api_key: string }>;
  specs: Record<string, { name: string; description: string; parameters: Record<string, any> }[]>;

  constructor(worker: Worker, runId: string, models: RemoteHost["models"], specs: RemoteHost["specs"]) {
    this.worker = worker;
    this.runId = runId;
    this.models = models;
    this.specs = specs;
  }

  model(resource: string, step: string, settings: Record<string, any>): any {
    const found = this.models[`${resource}|${step}`];
    if (!found) throw new Error(`the step '${step}' has no AI model '${resource}' wired in`);
    return model(found.base_url, found.api_key, settings);
  }

  tools(resources: string[], step: string): ToolSpec[] {
    return resources.flatMap((resource) => (this.specs[resource] ?? []).map((spec) => ({
      ...spec,
      call: async (args: Record<string, any>) => {
        const out = await this.worker.call(this.runId, "tool", { resource, tool: spec.name, step, args });
        return typeof out === "string" ? out : JSON.stringify(out);
      },
    })));
  }

  search(knowledgeBase: string, query: string, opts: { mode: string; top_k: number; min_score: number }) {
    return this.worker.call(this.runId, "search", { knowledge_base: knowledgeBase, query, ...opts });
  }

  script(importPath: string, state: Record<string, any>) {
    return this.worker.call(this.runId, "script", { import_path: importPath, state });
  }
}

const worker = new Worker();
const version = JSON.parse(readFileSync(join(HERE, "node_modules/@mastra/core/package.json"), "utf8")).version;
send({ ready: true, version });
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  if (!line.trim()) continue;
  const msg = JSON.parse(line);
  if ("reply" in msg) worker.reply(msg);
  else if (msg.op === "run") void worker.run(msg);
  else if (msg.op === "cancel") worker.runs.get(msg.run)?.abort();
}
process.exit(0); // the server went away
