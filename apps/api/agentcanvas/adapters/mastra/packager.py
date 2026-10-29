"""Export a Mastra flow as a TypeScript project of its own (S36).

The project holds the generated flow (`src/workflow.ts`), the runtime it runs on (`flow.ts` and `runtime.ts`,
vendored), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a
paused run in a file and continues it, and tests (`node --test`) that replay the workflow's saved test cases with a
scripted model. Node.js 22.18+ runs it as it is: no build step. Workspace code is Python, so a flow with tools, code
steps or knowledge bases calls it through a small Python helper (`python/sidecar.py`, run with python3).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from agentcanvas.adapters.langchain.packager import _exportable_cases, _secret_names, package_name
from agentcanvas.adapters.mastra import codegen
from agentcanvas.adapters.openai_agents.packager import model_specs
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.validation.analysis import refuse_plain_secrets
from agentcanvas.workspace import bundle

API_ROOT = Path(__file__).parents[2]  # the `agentcanvas` package directory
PYTHON_TYPES = {"tool.python", "core.script", "knowledge.search"}

HOST = """/**
 * Where the flow's steps get their models, tools, knowledge bases and code when it runs on its own.
 *
 * Each model block reaches its provider over the OpenAI chat-completions API. Workspace code (tools, code steps) and
 * knowledge-base search are Python: `python/sidecar.py` runs them, started with python3 (or $PYTHON) when the flow
 * first needs it.
 */

import { spawn } from "node:child_process";
import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

import { createOpenAICompatible } from "@ai-sdk/openai-compatible";

import type { Host as FlowHost, ToolSpec } from "./runtime.ts";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const MODELS: Record<string, { model: string; base_url: string; key_env: string }> = __MODELS__;
export const TOOLS: Record<string, string> = __TOOLS__; // tool block -> its Python import path

/** `mocks` (model block -> replies, as saved test cases have them) script the models instead of calling them. */
export class Host implements FlowHost {
  mocks: Record<string, any[]>;
  specs: Record<string, { name: string; description: string; parameters: Record<string, any> }[]> = {};
  python: Python | null = null;

  constructor(mocks: Record<string, any[]> = {}) {
    this.mocks = mocks;
  }

  /** A host whose tools are described (their names, descriptions and arguments come from the Python code). */
  static async create(mocks: Record<string, any[]> = {}): Promise<Host> {
    const host = new Host(mocks);
    if (Object.keys(TOOLS).length) host.specs = await host.sidecar().call({ op: "describe", tools: TOOLS });
    return host;
  }

  sidecar(): Python {
    this.python ??= new Python();
    return this.python;
  }

  close(): void {
    this.python?.close();
  }

  model(resource: string, step: string, settings: Record<string, any>): any {
    if (resource in this.mocks) return scriptedModel(this.mocks[resource]);
    const spec = MODELS[resource];
    const env = resource.toUpperCase();
    return createOpenAICompatible({
      name: resource,
      baseURL: process.env[`${env}_BASE_URL`] || process.env.MODEL_BASE_URL || spec.base_url,
      apiKey: process.env[spec.key_env] || "not-needed",
      supportsStructuredOutputs: true,
      fetch: withSettings(settings),
    }).chatModel(process.env[`${env}_MODEL`] || spec.model);
  }

  tools(resources: string[], step: string): ToolSpec[] {
    return resources.flatMap((resource) => (this.specs[resource] ?? []).map((spec) => ({
      ...spec,
      call: async (args: Record<string, any>) => {
        const out = await this.sidecar().call({ op: "tool", path: TOOLS[resource], tool: spec.name, args });
        return typeof out === "string" ? out : JSON.stringify(out);
      },
    })));
  }

  search(knowledgeBase: string, query: string, opts: { mode: string; top_k: number; min_score: number }) {
    return this.sidecar().call({ op: "search", knowledge_base: knowledgeBase, query, ...opts });
  }

  script(importPath: string, state: Record<string, any>) {
    return this.sidecar().call({ op: "script", path: importPath, state });
  }
}

/** A `fetch` that adds a model block's settings to each chat request (the OpenAI names). */
export function withSettings(settings: Record<string, any>, base: typeof fetch = fetch): typeof fetch {
  const extra: Record<string, any> = {};
  if (settings.temperature !== undefined && settings.temperature !== null) extra.temperature = settings.temperature;
  if (settings.maxTokens !== undefined && settings.maxTokens !== null) extra.max_tokens = settings.maxTokens;
  if (!Object.keys(extra).length) return base;
  return (async (url: any, init?: any) => {
    if (init && typeof init.body === "string") {
      init = { ...init, body: JSON.stringify({ ...JSON.parse(init.body), ...extra }) };
    }
    return base(url, init);
  }) as typeof fetch;
}

/**
 * Scripted replies, as the canvas's mock mode gives them: the n-th model turn of an agent's run gets reply n (the last
 * one repeats). A reply is text, or {"content": ..., "tool_calls": [{"name": ..., "args": {...}}]}. They are answered
 * by an OpenAI-compatible endpoint in memory, so the model is the one Mastra always uses and nothing reaches a
 * provider.
 */
export function scriptedModel(replies: any[]): any {
  const answer = async (_url: any, init?: any) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    const turn = (body.messages ?? []).filter((m: any) => m.role === "assistant").length;
    const given = replies.length ? replies : [""];
    const reply = given[Math.min(turn, given.length - 1)];
    const calls: any[] = typeof reply === "object" && reply !== null ? reply.tool_calls ?? [] : [];
    const message: Record<string, any> = { role: "assistant",
      content: typeof reply === "object" && reply !== null ? reply.content ?? "" : String(reply) };
    if (calls.length) {
      message.tool_calls = calls.map((c, i) => ({ id: `mock_${turn}_${i}`, type: "function",
        function: { name: c.name, arguments: JSON.stringify(c.args ?? {}) } }));
    }
    return new Response(JSON.stringify({ id: `mock_${turn}`, object: "chat.completion", created: 0, model: "scripted",
      choices: [{ index: 0, message, finish_reason: calls.length ? "tool_calls" : "stop" }],
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 } }),
    { headers: { "content-type": "application/json" } });
  };
  return createOpenAICompatible({ name: "scripted", baseURL: "http://scripted/v1", apiKey: "scripted",
    supportsStructuredOutputs: true, fetch: answer as typeof fetch }).chatModel("scripted");
}

/** The Python helper: one request per line on its stdin, one answer per line on its stdout. */
class Python {
  proc: ChildProcessWithoutNullStreams;
  waiting: { resolve: (v: any) => void; reject: (e: Error) => void }[] = [];

  constructor() {
    this.proc = spawn(process.env.PYTHON || "python3", [join(ROOT, "python", "sidecar.py")],
      { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });
    this.proc.stderr.pipe(process.stderr);
    createInterface({ input: this.proc.stdout }).on("line", (line) => {
      const msg = JSON.parse(line);
      const next = this.waiting.shift();
      if (next && "error" in msg) next.reject(new Error(msg.error));
      else next?.resolve(msg.value ?? null);
    });
    this.proc.on("exit", () => {
      for (const w of this.waiting.splice(0)) w.reject(new Error("the Python helper stopped"));
    });
  }

  call(request: Record<string, any>): Promise<any> {
    return new Promise((resolve, reject) => {
      this.waiting.push({ resolve, reject });
      this.proc.stdin.write(`${JSON.stringify(request)}\\n`);
    });
  }

  close(): void {
    this.proc.stdin.end();
  }
}
"""

MAIN = """/**
 * `node src/main.ts '{"input": {...}, "thread_id": "t1"}'`: one run of the flow, its events as JSON lines.
 *
 * A paused run is saved in `.runs/<thread_id>.json` (or $RUNS_DIR); `{"thread_id": ..., "resume": <answer>}`
 * continues it with a person's answer. `"mocks": {"<model block>": ["reply", ...]}` scripts the models (no keys, no
 * network).
 */

import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { Host } from "./host.ts";
import * as rt from "./runtime.ts";
import { FLOW } from "./workflow.ts";

const saved = (threadId: string) => join(process.env.RUNS_DIR || ".runs", `${threadId}.json`);

/** Run (or continue) the flow; returns its last event (RUN_FINISHED or RUN_ERROR). */
export async function runOnce(input: Record<string, any> | null, opts: { threadId?: string; resume?: any;
  mocks?: Record<string, any[]>; quiet?: boolean } = {}): Promise<rt.Event> {
  const threadId = opts.threadId ?? "t1";
  let resume: [rt.Paused, any] | null = null;
  if ("resume" in opts && opts.resume !== undefined) {
    if (!existsSync(saved(threadId))) throw new Error(`nothing to continue on thread '${threadId}'`);
    resume = [JSON.parse(readFileSync(saved(threadId), "utf8")), opts.resume];
  }
  const host = await Host.create(opts.mocks ?? {});
  let last: rt.Event = {};
  try {
    for await (const ev of rt.run(FLOW, host, { input, resume, runId: threadId })) {
      const paused = ev.paused;
      delete ev.paused;
      if (paused !== undefined) {
        mkdirSync(join(saved(threadId), ".."), { recursive: true });
        writeFileSync(saved(threadId), JSON.stringify(paused));
      } else if (ev.type === "RUN_FINISHED") {
        rmSync(saved(threadId), { force: true }); // a failed continue keeps the paused run, to try again
      }
      if (!opts.quiet) console.log(JSON.stringify(ev));
      last = ev;
    }
  } finally {
    host.close();
  }
  return last;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const req = JSON.parse(process.argv[2] || "{}");
  await runOnce(req.input ?? null, { threadId: req.thread_id, resume: req.resume, mocks: req.mocks });
}
"""

TESTS = """// Generated with the project: the flow loads, and saved test cases that script every model pass.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { runOnce } from "../src/main.ts";
import { FLOW } from "../src/workflow.ts";

const CASES = JSON.parse(readFileSync(new URL("./test_cases.json", import.meta.url), "utf8"));

test("the flow loads", () => {
  assert.ok(Object.keys(FLOW.steps).length);
  for (const id of FLOW.start) assert.ok(id in FLOW.steps);
});

for (const c of CASES) {
  test(`saved case: ${c.name}`, async () => {
    process.env.RUNS_DIR = mkdtempSync(join(tmpdir(), "runs-"));
    const last = await runOnce(c.input, { mocks: c.mocks, quiet: true, threadId: "case" });
    const status = ({ success: "success", interrupt: "interrupted" } as Record<string, string>)[last.outcome]
      ?? "error";
    const expect = c.expect ?? {};
    assert.equal(status, expect.status ?? "success", JSON.stringify(last));
    for (const [name, value] of Object.entries(expect.channels ?? {})) {
      assert.deepEqual(last.output?.[name], value, name);
    }
  });
}
"""

SIDECAR = '''"""The Python helper of an exported Mastra flow: runs workspace tools and code steps, describes the
tools, and searches knowledge bases. One JSON request per line on stdin, one `{"value": ...}` or `{"error": ...}`
per line on stdout."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PROTOCOL = sys.stdout
sys.stdout = sys.stderr  # anything the code prints goes to stderr

from agentcanvas import knowledge_search as ks  # noqa: E402

KNOWLEDGE_DIR = HERE.parent / "knowledge"
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
_kbs: dict[str, Any] = {}


def load(path: str) -> Any:
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def describe(found: Any) -> list[dict[str, Any]]:
    """A tool's name, description and JSON-schema arguments (a list of them for a list of tools)."""
    if isinstance(found, list):
        return [d for f in found for d in describe(f)]
    if hasattr(found, "ainvoke"):  # a LangChain tool
        schema = getattr(found, "tool_call_schema", None) or getattr(found, "args_schema", None)
        params = schema.model_json_schema() if hasattr(schema, "model_json_schema") else {"type": "object"}
        return [{"name": found.name, "description": found.description or found.name, "parameters": params}]
    props, required = {}, []
    for name, p in inspect.signature(found).parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        hint = JSON_TYPES.get(p.annotation) or {t.__name__: v for t, v in JSON_TYPES.items()}.get(str(p.annotation))
        props[name] = {"type": hint} if hint else {}
        if p.default is p.empty:
            required.append(name)
    doc = inspect.getdoc(found) or found.__name__
    return [{"name": found.__name__, "description": doc, "parameters": {"type": "object", "properties": props,
                                                                        "required": required}}]


def tool(found: Any, name: str, args: dict[str, Any]) -> Any:
    if isinstance(found, list):  # a block with several tools
        found = next(f for f in found if getattr(f, "name", getattr(f, "__name__", "")) == name)
    try:  # a tool's error is told to the model, as inside AgentCanvas
        out = found.ainvoke(args) if hasattr(found, "ainvoke") else found(**args)
        out = asyncio.run(out) if inspect.isawaitable(out) else out
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    content = getattr(out, "content", out)
    return content if isinstance(content, str) else json.dumps(content, default=str)


def search(req: dict[str, Any]) -> list[dict[str, Any]]:
    name = req["knowledge_base"]
    if name not in _kbs:
        path = Path(os.environ.get("AGENTCANVAS_KNOWLEDGE_DIR") or KNOWLEDGE_DIR) / f"{name}.jsonl"
        if not ks.NAME.match(name) or not path.is_file():
            raise ValueError(f"knowledge base '{name}' not found (looked for {path})")
        header, chunks = ks.load_jsonl(path.read_text(encoding="utf-8"))
        if (header.get("embedding", {}).get("model") or ks.BUILTIN) != ks.BUILTIN:
            for c in chunks:  # vectors of a provider's embedding model: search by words here
                c["vector"] = None
        _kbs[name] = ks.Index(chunks)
    query, mode = req["query"], req["mode"]
    vector = ks.builtin_embed([query])[0] if mode != "keyword" and query.strip() else None
    return ks.search(_kbs[name], query, mode=mode, top_k=req["top_k"], min_score=req["min_score"],
                     query_vector=vector)


def answer(req: dict[str, Any]) -> Any:
    if req["op"] == "describe":
        return {block: describe(load(path)) for block, path in req["tools"].items()}
    if req["op"] == "tool":
        return tool(load(req["path"]), req.get("tool", ""), dict(req["args"]))
    if req["op"] == "script":
        out = load(req["path"])(req["state"])
        return dict((asyncio.run(out) if inspect.isawaitable(out) else out) or {})
    if req["op"] == "search":
        return search(req)
    raise ValueError(f"unknown request '{req['op']}'")


for line in sys.stdin:
    if not line.strip():
        continue
    try:
        reply: dict[str, Any] = {"value": answer(json.loads(line))}
    except Exception as exc:  # noqa: BLE001 - the step that asked fails with it
        reply = {"error": f"{type(exc).__name__}: {exc}"}
    PROTOCOL.write(json.dumps(reply, default=str) + "\\n")
    PROTOCOL.flush()
'''

TSCONFIG = {
    "compilerOptions": {
        "target": "es2023",
        "module": "nodenext",
        "moduleResolution": "nodenext",
        "allowImportingTsExtensions": True,
        "erasableSyntaxOnly": True,
        "noEmit": True,
        "skipLibCheck": True,
    },
    "include": ["src", "tests"],
}


def _readme(wf: Workflow, pkg: str, models: dict[str, dict[str, Any]], python: bool) -> str:
    env = "\n".join(
        f"| `{name}` | `{m['model']}` | `{m['base_url']}` | `{m['key_env']}` | `{name.upper()}_MODEL`, "
        f"`{name.upper()}_BASE_URL` |"
        for name, m in models.items()
    )
    workspace = (
        """
## Workspace code

The workflow's tools, code steps and knowledge bases are Python, as on the canvas: `python/sidecar.py` runs them
with `python3` (or `$PYTHON`) next to the flow. Install what the code needs first:

```bash
python3 -m pip install -r python/requirements.txt
```
"""
        if python
        else ""
    )
    return f"""# {wf.name}

A [Mastra](https://mastra.ai/) flow exported from AgentCanvas, in TypeScript. The flow is in `src/workflow.ts`: a
Mastra `Agent` for each AI block (an AI SDK model, `createTool` tools with `requireApproval` for the ones a person
approves first, a Zod schema for typed answers), an `async function` for each block of the canvas and a `FLOW` that
says which step follows which. `src/flow.ts` runs it as the canvas draws it (steps side by side in the same round,
"wait for all" steps, paths by CEL rules, loops with a limit, field reducers) and `src/runtime.ts` adds the Mastra
parts: pauses for people, and tool calls that wait for approval (the agent's suspended run is saved with the pause).

Node.js {codegen.NODE_VERSION} or later runs the TypeScript as it is: there is no build step.

```bash
npm install
cp .env.example .env    # then fill in your keys, and `set -a; . ./.env; set +a`
node src/main.ts '{{"input": {{}}}}'
```

A run that pauses (an approval, a question or a tool call to approve) prints what it waits for and saves itself in
`.runs/<thread>.json`; continue it with the answer:

```bash
node src/main.ts '{{"thread_id": "t1", "resume": {{"approved": true}}}}'
```

## Models

Each model block reaches its provider over the OpenAI chat-completions API (any provider with such an endpoint,
local servers included). Override the model or address per block with the variables in the last column. Backup
models and the answer cache of the canvas are not exported for Mastra flows.

| Block | Model | Address | Key variable | Overrides |
|---|---|---|---|---|
{env}
{workspace}
## Tests

`npm test` replays the workflow's saved test cases with a scripted model: no keys, no network.

Change the workflow on the canvas and export it again rather than editing the generated files.
"""


def package(
    wf: Workflow,
    source: str,
    *,
    workspace: Path | None = None,
    test_cases: list[dict[str, Any]] | None = None,
    version: str = "0.0.0",
) -> tuple[str, bytes]:
    """(file name, zip bytes) of the standalone TypeScript project for `wf`, whose generated flow is `source`."""
    refuse_plain_secrets(wf)
    pkg = package_name(wf.name)
    root = f"{pkg.replace('_', '-')}-mastra"
    models = model_specs(wf)
    for m in models.values():
        m.pop("fallbacks", None)  # not exported for these flows (see the README)
    tools = {r.name: r.config["import_path"] for r in wf.resources if r.type == "tool.python"}
    python = any(el.type in PYTHON_TYPES for el in [*wf.nodes, *wf.resources])
    found = bundle.collect(workspace, wf.id, wf.model_dump_json(), source) if workspace is not None else None
    if found is not None and found.missing:
        raise InvalidInput(
            "the workflow uses code that is not in the workspace: " + ", ".join(found.missing)
            + "; create the file or fix the step's import path, then export again"
        )  # fmt: skip
    keys = sorted({m["key_env"] for m in models.values()} | _secret_names(wf.model_dump(mode="json")))
    manifest = {
        "name": pkg.replace("_", "-"),
        "version": version,
        "description": f"{wf.name} — a Mastra flow exported from AgentCanvas",
        "private": True,
        "type": "module",
        "engines": {"node": f">={codegen.NODE_VERSION}"},
        "scripts": {"start": "node src/main.ts", "test": "node --test tests/workflow.test.ts"},
        "dependencies": dict(sorted(codegen.PACKAGES.items())),
    }
    host = HOST.replace("__MODELS__", json.dumps(models, indent=2)).replace("__TOOLS__", json.dumps(tools, indent=2))
    files: dict[str, str | bytes] = {
        "package.json": json.dumps(manifest, indent=2) + "\n",
        "tsconfig.json": json.dumps(TSCONFIG, indent=2) + "\n",
        "README.md": _readme(wf, pkg, models, python),
        ".env.example": "".join(f"{k}=\n" for k in keys),
        ".gitignore": "node_modules/\n.env\n.runs/\n__pycache__/\n",
        "src/workflow.ts": source,
        "src/host.ts": host,
        "src/main.ts": MAIN,
        "tests/workflow.test.ts": TESTS,
        "tests/test_cases.json": json.dumps(_exportable_cases(wf, test_cases or []), indent=2) + "\n",
    }
    for name in ("flow.ts", "runtime.ts"):
        files[f"src/{name}"] = (API_ROOT / "adapters" / "mastra" / name).read_bytes()
    if python:
        files["python/sidecar.py"] = SIDECAR
        files["python/agentcanvas/__init__.py"] = '"""The parts of AgentCanvas the flow\'s Python helper uses."""\n'
        files["python/agentcanvas/knowledge_search.py"] = (API_ROOT / "knowledge_search.py").read_bytes()
        files["python/requirements.txt"] = "".join(f"{r}\n" for r in (found.requirements if found else []))
    for rel in found.files if found else []:
        files[f"python/{rel}"] = (workspace / rel).read_bytes()  # pyright: ignore[reportOptionalOperand]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{root}/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, content)
    return f"{root}-{version}.zip", buf.getvalue()
