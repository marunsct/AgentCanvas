# The Copilot's real-model evaluation

The repository has a GitHub Actions job, `copilot-evals`, that tests the AI Copilot against a real language model
every night. It is optional: without a provider key it reports "skipped" and stops, and nothing else depends on it.
This page says what it does, why it is worth having, and how to turn it on.

## What it does

The job lives in `.github/workflows/copilot-evals.yml` and runs the harness in `apps/api/agentcanvas/copilot/evals.py`.
It runs at 03:17 UTC every night, or whenever someone starts it by hand. Each run tests both Copilot engines (the
built-in loop and Deep Agents) side by side, each through two suites:

| Suite | Cases | What a case asks | A case passes when |
|---|---|---|---|
| `build` | 10 | Build a workflow from a plain request, such as "classify support tickets as urgent or not; urgent ones need a manager's approval" | the proposal applies to the canvas, has no validation errors, and uses the expected kinds of block (a router, an approval step, …) |
| `debug` | 137 (it grows with the templates) | Fix a starter template with a planted bug: a missing arrow to Finish, an unplugged model, a wrong output field, a broken condition, an unknown field in a prompt | the Copilot's fix leaves no validation errors |

A suite fails the job when fewer than 70% of its cases pass. Each engine's results (pass or fail, tokens, seconds
and the reason for each failure) are kept as an artifact, `copilot-evals-loop` or `copilot-evals-deepagents`, for
30 days.

## Why it is worth having

The ordinary test suite drives the Copilot with a scripted fake model. That proves the code works: tools are
called, proposals are stored, accept and undo behave. It cannot tell whether a real model, given our prompts and
tools, builds good workflows. Quality can slip without any of our tests failing:

- **A prompt or tool description changes.** A reworded instruction can make the model skip a step or choose the
  wrong block.
- **A library is upgraded.** LangChain, LangGraph and Deep Agents change how messages and tool calls are passed.
- **The provider changes its model**, or a model is retired and replaced.
- **The block catalogue grows.** New blocks and templates make the Copilot's choices harder.

The nightly run catches these within a day, with a concrete list of the requests that now fail, instead of a user
noticing weeks later. It also answers one open decision with data: Deep Agents becomes the default engine once it
does at least as well as the loop (see [decisions.md](../decisions.md), "The Copilot work so far").

## How to turn it on

You need a repository admin on GitHub and an API key from Anthropic (or OpenAI).

1. **Create a key** at console.anthropic.com (Settings → API keys). Give it a monthly spending limit there; that
   caps what the job can ever cost.
2. **Add it to the repository**: on GitHub, open the repository → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**. Name it `ANTHROPIC_API_KEY` and paste the key.
3. **Optionally choose the model**: on the same page, under the **Variables** tab, add `COPILOT_EVAL_MODEL` with a
   value such as `anthropic:claude-sonnet-5-5`. Without it the job uses `anthropic:claude-haiku-5-5`, the cheapest
   option. Without a key the job still ends green, with a "Copilot evaluation skipped" notice and a line in the run's
   summary. To use OpenAI instead, add the secret `OPENAI_API_KEY` and set `COPILOT_EVAL_MODEL` to an OpenAI model,
   such as `openai:gpt-5-mini`.
4. **Try it once**: **Actions** → **copilot-evals** → **Run workflow**. The optional `model` field overrides the
   variable for that run only.

The key is safe there. GitHub never prints secrets in logs, and the job runs only on its schedule or when started
by hand, never for pull requests, so code from a fork cannot reach the key. The job can read the repository but not
change it.

## Reading the results

Open the run in **Actions**. Each engine's log ends with lines such as:

```
PASS triage          18342 tok  21.4s
FAIL rag             25110 tok  30.2s missing core.memory_recall
9/10 passed (deepagents, anthropic:claude-haiku-4-5, build)
```

A red run means a suite fell below 70%. Download the engine's artifact for the full JSON. A failure that every
model shares usually points at our prompts or tools rather than at the model.

## Results so far

**2026-11-09, on this machine:** `qwen3.6-35b-a3b-mtp` (Qwen3.6 35B-A3B, 4-bit) served by LM Studio on two Radeon
RX 7900 GPUs (`MODEL=lmstudio:qwen3.6-35b-a3b-mtp`), both suites, both engines, no hosted model and no cost.

| Engine | build | debug | time (debug) | tokens per case |
|---|---|---|---|---|
| Loop (default) | 7 of 10 | 136 of 137 | about 12 min | about 70,000 (the debug suite's 9.7M over 137 cases) |
| Deep Agents | 7 of 10 | 135 of 137 | about 32 min | 1.5 to 3.6 times the loop's on the cases rerun |

- The build cases both engines missed are "greeter", "per_topic" and "mcp" for the loop (a step or block the case
  expects was missing); Deep Agents missed others, each time stopping at its step limit.
- The debug figures include the three "Lead scoring with research" cases, rerun after a harness fix (the template's
  own block was unknown to the harness, so they could not pass before); all three now pass on both engines.
- Deep Agents reached its step limit on most cases, also ones it passed. Its token counts were zero on LM Studio
  until models on OpenAI-compatible servers were asked for their streamed usage (fixed the same day).

**Decision:** the loop engine stays the default. The two engines pass the same share of cases, but Deep Agents takes
about 2.7 times as long, uses more tokens and works up to its step limit. Revisit with a hosted model (the nightly job)
or after tuning Deep Agents' step budget; the switch is ready on the branch `pending/copilot-deep-agents-default`.

## Running it on your own computer

The same harness runs locally with any model the app supports, including a local one through Ollama. You don't
need GitHub or a repository secret:

```
cd apps/api
export ANTHROPIC_API_KEY=…                     # not needed for a local model
uv run python -m agentcanvas.copilot.evals --model anthropic:claude-haiku-5-5 --engine deepagents --suite build
uv run python -m agentcanvas.copilot.evals --model ollama:qwen3 --engine loop --suite debug --out debug.json
uv run python -m agentcanvas.copilot.evals --model ollama:qwen3 --case greeter     # one case
```

The shortcut for all of this is one command from the repository root:

```
make copilot-evals                                             # Ollama qwen3, both suites, the loop engine
make copilot-evals MODEL=ollama:llama3.3 ENGINE=deepagents     # another local model, the other engine
make copilot-evals MODEL=anthropic:claude-sonnet-5-5 OUT=sonnet.json   # a hosted model (its key in the environment)
```

It runs the `build` and `debug` suites one after the other and writes one JSON report to `OUT` (default
`copilot-evals.json` in the repository root, which Git ignores): `{"model", "engine", "build": [...], "debug": [...]}`
with the same per-case fields as the nightly artifacts. `MODEL` defaults to `ollama:qwen3`.

When the model cannot be reached (Ollama is not running, or the hosted model's API key is not set) the command says
so in a `NOTE:` line and runs the **offline demo Copilot** instead, so the target always finishes. The demo Copilot
builds a one-step greeter and nothing else: expect about 2 of 10 build cases and none of the debug cases to pass. That
shows the harness works from end to end on your machine; it is not a score for any model. Calling the harness
directly (`uv run python -m agentcanvas.copilot.evals`) never falls back unless you pass `--fallback-to-demo`, so a
nightly run with a missing key stays red instead of passing on the demo.

Local models are slower, especially without a GPU, and small ones often fail both engines on tool use. Their
results tell you how the Copilot copes with that model, which matters if your users run local models, but they
are not a fair stand-in for the hosted models most people choose.

## Turning it off or making it cheaper

- **To turn it off**, delete the secret; the job goes back to "skipped".
- **To run it only on demand**, remove the `schedule:` block from the workflow file. **Run workflow** still works.
- **To spend less**, keep the default Haiku model, or remove `deepagents` from the job's `matrix.engine` list once
  the default engine has been decided.
