/**
 * The runtime for workflows designed for Mastra (S36).
 *
 * A workflow becomes plain TypeScript (see `codegen.py`): one function per block, Mastra `Agent`s for the AI blocks,
 * and a `FLOW` that the flow engine (`flow.ts`) runs as the canvas draws it. This module adds the Mastra parts:
 * models (an AI SDK model over an OpenAI-compatible address), tools (`createTool`, with `requireApproval` for the
 * tools a person approves first), typed answers (`structuredOutput` with a Zod schema) and model call limits
 * (`maxSteps`). A tool call that waits for approval suspends the agent's run, Mastra's own way: the run's snapshot is
 * saved with the pause, and the agent continues from it with `approveToolCallGenerate` or `declineToolCallGenerate`
 * once a person has approved, edited or rejected the call.
 *
 * The same module runs inside AgentCanvas (in a Node.js worker) and in exports. What differs is the `Host`, which gives
 * steps their models, tools, knowledge bases and workspace code.
 */

import { Mastra } from "@mastra/core";
import { Agent } from "@mastra/core/agent";
import { InMemoryStore } from "@mastra/core/storage";
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import * as engine from "./flow.ts";
import type { Flow, Host, Paused, State, Update } from "./flow.ts";

export { conversation, Interrupted, render, StopRun } from "./flow.ts";
export type { Event, Flow, Host, Paused, State, Step, ToolSpec, Update } from "./flow.ts";

/** Run `flow` from the start with `input`, or continue a paused run with a person's answer (see `flow.ts`). */
export function run(flow: Flow, host: Host, opts: { input?: State | null; resume?: [Paused, any] | null;
  runId?: string } = {}): AsyncGenerator<engine.Event> {
  return engine.run(flow, host, Run, opts);
}

type AgentOptions = {
  output?: string;
  answer?: z.ZodType; // the typed answer's schema
  maxCalls?: number;
  stopAtLimit?: boolean;
};

type Saved = { runId: string; toolCallId: string; snapshots: Record<string, any>[] };

/** What one step can do: Mastra models, tools and agents, people, knowledge bases and workspace code. */
export class Run extends engine.Run {
  edits = new Map<string, Record<string, any>>(); // tool call id -> the arguments a person wrote

  // ---- models and tools ----
  /** The model for the canvas model block `resource` (with `temperature`, `maxTokens`). */
  model(resource: string, settings: Record<string, any> = {}): any {
    return this.host.model(resource, this.name, settings);
  }

  /** Mastra tools for the canvas tool blocks `resources`; the tools named in `approve` wait for a person. */
  tools(resources: string[], opts: { approve?: string[] } = {}): Record<string, any> {
    const out: Record<string, any> = {};
    for (const spec of this.host.tools(resources, this.name)) {
      out[spec.name] = createTool({
        id: spec.name,
        description: spec.description,
        inputSchema: z.fromJSONSchema(spec.parameters as any) as any,
        requireApproval: (opts.approve ?? []).includes(spec.name),
        execute: async (input: any, ctx: any) => spec.call(this.edits.get(ctx?.agent?.toolCallId) ?? input ?? {}),
      });
    }
    return out;
  }

  // ---- AI ----
  /** One answer from `agent` to `prompt` (an "Ask AI" step). */
  async text(agent: Agent, prompt: string): Promise<string> {
    const options = this.host.signal ? { abortSignal: this.host.signal } : {};
    return String((await agent.generate(prompt, options)).text ?? "").trim();
  }

  /**
   * An agent's turn in the conversation `messages`: its answer is appended to `output` (and, with an `answer`
   * schema, also written to `structured_response`). Tools that need approval pause the run here. After `maxCalls`
   * model calls the agent stops with a note (or, `stopAtLimit: false`, the run fails).
   */
  async agent(agent: Agent, messages: any, opts: AgentOptions = {}): Promise<Update> {
    const output = opts.output ?? "messages";
    const name = agent.name || this.name;
    const options: Record<string, any> = {};
    if (opts.answer) options.structuredOutput = { schema: opts.answer };
    if (opts.maxCalls !== undefined) options.maxSteps = opts.maxCalls;
    if (this.host.signal) options.abortSignal = this.host.signal;
    const store = new InMemoryStore();
    const mastra = new Mastra({ agents: { agent }, storage: store, logger: false });
    const running = mastra.getAgent("agent");
    let result: any;
    const saved: Saved | null = this.native ? JSON.parse(this.native) : null;
    const found = saved ? this.toolAnswers(1) : null;
    if (saved && found) {
      const workflows: any = await store.getStore("workflows");
      for (const s of saved.snapshots) await workflows.persistWorkflowSnapshot(s);
      const decision = found[0];
      const ids = { runId: saved.runId, toolCallId: saved.toolCallId };
      const edited = engine.editedArgs(decision);
      if (edited !== null) this.edits.set(saved.toolCallId, edited);
      if (edited !== null || (decision.type ?? "approve") === "approve") {
        result = await running.approveToolCallGenerate({ ...options, ...ids });
      } else {
        const reason = `Rejected by a person: ${decision.message || "no reason given"}`;
        result = await running.declineToolCallGenerate({ ...options, ...ids, reason });
      }
    } else {
      result = await running.generate(engine.conversation(messages), options);
    }
    if (result.finishReason === "suspended") {
      const asked = result.suspendPayload ?? {};
      const workflows: any = await store.getStore("workflows");
      const { runs } = await workflows.listWorkflowRuns({});
      const snapshots = runs.map((r: any) => ({ workflowName: r.workflowName ?? r.workflow_name,
        runId: r.runId ?? r.run_id, snapshot: r.snapshot, resourceId: r.resourceId ?? r.resource_id }));
      const native = JSON.stringify({ runId: result.runId, toolCallId: asked.toolCallId, snapshots });
      throw this.toolRequest([[asked.toolName, asked.args ?? {}]], native);
    }
    if (opts.maxCalls !== undefined && result.finishReason === "tool-calls"
      && (result.steps?.length ?? 0) >= opts.maxCalls) {
      if (opts.stopAtLimit === false) {
        throw new engine.StopRun("model_call_limit",
          `stopped: '${this.name}' asked its model more than ${opts.maxCalls} times`);
      }
      return this.limitNote(name, opts.maxCalls, { output });
    }
    let answer: any = opts.answer ? result.object ?? parsed(result.text, opts.answer) : null;
    if (answer === null || answer === undefined) answer = String(result.text ?? "").trim();
    return this.reply(name, answer, { output, structured: Boolean(opts.answer) });
  }
}

/** The typed answer when the model wrote it as JSON text (else null). */
function parsed(text: string | undefined, schema: z.ZodType): any {
  try {
    const found = schema.safeParse(JSON.parse(text ?? ""));
    return found.success ? found.data : null;
  } catch {
    return null;
  }
}
