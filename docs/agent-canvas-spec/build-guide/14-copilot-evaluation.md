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
| `debug` | 104 | Fix a starter template with a planted bug: a missing arrow to Finish, an unplugged model, a wrong output field, a broken condition, an unknown field in a prompt | the Copilot's fix leaves no validation errors |

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
does at least as well as the loop (see `decisions.md`, "The Copilot work so far").

## How to turn it on

You need a repository admin on GitHub and an API key from Anthropic (or OpenAI).

1. **Create a key** at console.anthropic.com (Settings → API keys). Give it a monthly spending limit there; that
   caps what the job can ever cost.
2. **Add it to the repository**: on GitHub, open the repository → **Settings** → **Secrets and variables** →
   **Actions** → **New repository secret**. Name it `ANTHROPIC_API_KEY` and paste the key.
3. **Optionally choose the model**: on the same page, under the **Variables** tab, add `COPILOT_EVAL_MODEL` with a
   value such as `anthropic:claude-sonnet-5-5`. Without it the job uses `anthropic:claude-haiku-4-5`, the cheapest
   option. To use OpenAI instead, add the secret `OPENAI_API_KEY` and set `COPILOT_EVAL_MODEL` to an OpenAI model,
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

## Running it on your own computer

The same harness runs locally with any model the app supports, including a local one through Ollama. You don't
need GitHub or a repository secret:

```
cd apps/api
export ANTHROPIC_API_KEY=…                     # not needed for a local model
uv run python -m agentcanvas.copilot.evals --model anthropic:claude-haiku-4-5 --engine deepagents --suite build
uv run python -m agentcanvas.copilot.evals --model ollama:qwen3 --engine loop --suite debug --out debug.json
uv run python -m agentcanvas.copilot.evals --model ollama:qwen3 --case greeter     # one case
```

Local models are slower, especially without a GPU, and small ones often fail both engines on tool use. Their
results tell you how the Copilot copes with that model, which matters if your users run local models, but they
are not a fair stand-in for the hosted models most people choose.

## Turning it off or making it cheaper

- **To turn it off**, delete the secret; the job goes back to "skipped".
- **To run it only on demand**, remove the `schedule:` block from the workflow file. **Run workflow** still works.
- **To spend less**, keep the default Haiku model, or remove `deepagents` from the job's `matrix.engine` list once
  the default engine has been decided.
