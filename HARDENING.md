# Hardening plan

What is still open after the second deep review (2026-11-08) and the fixes that followed it. That review ran seven
read-only passes (run-time and sandbox; control plane and security; web app; Copilot, evaluation and knowledge;
adapters, exports and importers; infrastructure, operations and docs) and a check of standards, models and competitors.
It found about 110 defects, from one critical (another organisation's SCIM token could take over a pending invitation)
to many small ones; **all of them are fixed** on the hardening branch, each with a test that fails without it, a
changelog line, and the suites green on SQLite and Postgres. The review's per-area lists are in the commit messages
(ids CP-, RT-, W-, I-, P-, OP-, R-) and the first review's list is in git: `git show ad11a56:HARDENING.md`.

**How to use this file.** One PR per item; every fix lands with a test that fails without it. In the same PR, delete
the item here and add anything new you find. Items keep short ids so a PR title can name them.

---

## 1. Needs something this environment did not have

| Id | Item | Why it is open | To close it |
|---|---|---|---|
| O-1 | **Run the Copilot evaluation on a hosted model** (G-11) | Done on a local model on 2026-11-09 (LM Studio, Qwen3.6 35B-A3B on two Radeon GPUs): loop 7/10 build and 136/137 debug, Deep Agents 7/10 and 135/137 but 2.7× slower, with more tokens and at its step limit; the loop stays the default (`docs/agent-canvas-spec/build-guide/14-copilot-evaluation.md`). The nightly hosted-model job has never run (no GitHub runners) | `make copilot-evals MODEL=anthropic:claude-sonnet-5-5` for each engine when a key and runners exist; the Deep Agents switch waits on branch `pending/copilot-deep-agents-default` |
| O-2 | **Try the two integrations built from reference docs against the real services** | The Claude Managed Agents export (`ant apply` bundle) and the Git round trip (GitHub pull requests) were built from the reference documentation and tested with golden files and a mock transport; nothing here could call Anthropic or GitHub | Apply a bundle with the real `ant` CLI; connect a scratch repository; fix what differs and add it to the golden files |
| O-6 | **A person reading the product with a screen reader** | An automated scan (`e2e/axe.spec.ts`, axe-core, WCAG 2.1 A and AA, light and dark themes, every page and every editor tab) passes, and the inspector, menus and canvas are named and keyboard-reachable (`a11y-inspector.spec.ts`, `keyboard.spec.ts`). A machine cannot say whether the product is usable with NVDA, VoiceOver or JAWS | A screen-reader pass by a person; file what they find |

## 2. Improvement plan (from the 2026-11-08 market and standards check)

Ranked by what they add for the people who use AgentCanvas; sizes are S (days), M (a week or two), L (more).

| Id | Item | Why | Size |
|---|---|---|---|
| F-1 | **MCP Tasks on the deployment MCP endpoint, approvals through URL-mode elicitation** | A run that pauses for a person returns a task the caller can poll or cancel, and the approval is answered through a link instead of timing out. MCP SEP-2663 (Tasks, Beta, heading for core); AWS AgentCore Gateway, Mastra and LangSmith shipped it in October | M |
| F-2 | **MCP Server Card per deployment; A2A card caching headers and `application/a2a+json`** | Registries and clients discover deployments without a call; matches a2a-sdk 1.2/1.3 | S |
| F-3 | **Framework pin bumps, through the conformance kit** | Pydantic AI 2.66 has security fixes (URL-download blocklist gap, header leaks in spans); LangGraph 1.2.20 adds strict resume (wire its per-interrupt ids into the Inbox); ADK 2.14, Agent Framework 1.25 (Python ≥ 3.11), CrewAI 1.15.37, Strands 1.64, Agno 3.1.12, LlamaIndex Workflows 2.26.1, Mastra 1.82, Claude Agent SDK 0.2.179, `mcp` 2.3.0. Several have breaking changes; run `scripts/ci-local.sh frameworks` and the conformance kit for each | S each |
| F-4 | **Decide / classifier node** (FR-X-05) | A typed choice, probability or score through the gateway, also usable as an evaluator; OpenAI's Decisions API, ADK `ClassifierNode` and Strands `DecisionModel` all shipped in October | M |
| F-5 | **Kill switch and run priority** | An audited "stop all in-flight runs" per organisation or deployment, and a priority per run or trigger (LangSmith shipped both) | S/M |
| F-6 | **Repeated-run evaluation** | Each example k times: pass@k, variance, flakiness; argument matchers for tool calls; an automatic failure summary per experiment (ADK `--num_runs`, AgentCore failure analysis) | M |
| F-7 | ~~**Importer upkeep before 2026-11-30**~~ done 2026-11-09 (n8n 3.0 removals, all 27 Langflow 1.13 starters, 30 published Agent Builder exports) | n8n 3.0 removed node types (AI Agent v1 modes, LangChain Code, the vector store Insert/Load nodes, OpenAI Assistant): flag them with named replacements; check the Langflow importer against 1.13 exports; re-test the Agents SDK importer on a fresh Agent Builder export | S |
| F-8 | **AG-UI 1.0 endpoint for deployments** (revisits N-2 for deployments only) | AG-UI 1.0 is frozen and CopilotKit-style front ends, AgentCore and Agent Framework speak it | M |
| F-9 | **Publish a deployment to Slack, Microsoft Teams or email** | Per-person connectors and the Inbox already exist; enterprises want agents where people work (ADK `TeamsRunner`, n8n Teams, LangSmith Fleet) | M |
| F-10 | **"Schedule a follow-up" tool for agents** | An agent sets a one-off or recurring trigger on its own deployment, within policy and budget (OpenAI `automations`, Agno) | S |
| F-11 | **Natural-language run search filters and a weekly insights report** | "failed runs about refunds last week over $0.10" on top of the existing run search (LangSmith) | M |
| F-12 | **Custom roles and an erase-person action** | Org-defined permission sets for SCIM groups and tokens; GDPR erasure (LangSmith, Langflow 1.13) | M |
| F-13 | **Browser-use block in the sandbox** | A headless browser behind the egress allow-list, a screenshot per step, approval before submit; the most visible gap against hosted competitors (Gemini Computer Use GA, n8n Browser Use) | L |
| F-14 | **Live co-editing; voice** | N-3 and the audio part of N-4; large, after the rest | L |

## 2a. Left over from the 2026-11-08 fixes

- **Service connection:** a builder can still use the organisation's service connection by publishing and running the deployment (inherent in builders being allowed to publish). A schedule's own input may use any of the project's files.
- **Thread ids** stay one namespace across organisations: whether an id is taken is visible, not by whom (namespacing would change what callers get back).
- **Hosted edition:** only the OpenAI-compatible client is pinned to public addresses; other providers with their own address are refused there. No pinned sync client. The design-export kit (`designs/kit/canvas_tools.py`) still uses plain `is_global` (it cannot import AgentCanvas).
- **Usage after a stop:** the CrewAI and OpenAI Agents adapters drain usage like the LangGraph one did before RT-4.
- **Model catalog:** GPT-5.6 and Gemini 3.8 Flash have their published prices (2026-11-09) but no capability flags (vision, sampling) yet. GPT-5.6 Sol's price is promotional until 2026-11-30 and its later price is not published: recheck then. Gemini 3.8 Flash is recorded at its 2027 price (double the introductory one), so budgets err on the safe side until then. `grok-4` has no retire date; Haiku 5.5 cache prices are derived from the stated multipliers.
- **Operations ideas not done:** a startup check that migrations are at head when `AUTO_MIGRATE` is off; a generated reference page of every server setting; HSTS and Permissions-Policy headers in the web image.
- **Web:** the Run panel and chat input still re-render on every streamed token (they show the text).

## 3. Decided not to build (yet), with the reason

| Id | Item | Reason |
|---|---|---|
| N-2 | AG-UI wire for the Copilot, a Copilot checkpointer with time travel, a per-thread `/run` route (PLAN.md) | Would run beside the SSE format the web already reads, with no gain except CopilotKit interop, which the plan itself rejects. Revisit if interop becomes a goal |
| N-3 | Live co-editing (merging simultaneous edits, cursors) | Presence is built (who is here, which block, "saved a newer version"); merging edits needs a CRDT over the IR with the command log kept as the source of truth |
| N-4 | Interactive multi-turn red teaming (an attacker model reacting to each reply), audio and video ports, files for Planning Agents, file downloads for share visitors | Scope; documented in the guide pages for each |
| N-5 | OAuth connectors in exported code | A workflow exported as code cannot sign people in (the AgentCanvas server holds the connections); the export fails with a clear message. (Hand-entered client credentials and the schedule warning are done.) |
| N-6 | A memory embedding model per project | The LangGraph store's vector column has one size per store, so the model is a server setting (`AGENTCANVAS_MEMORY_EMBEDDING`). Per project would need a store per vector size |
| N-7 | Decrypting secrets per project with a short lifetime instead of one cache per API process | Code that could read the cache could also read the KMS key; no tenant code runs in the API process (the sandbox is required in staging and production). See `decisions.md` |

## 4. Carried over

- **Cross-organisation sweep coverage:** the crawl reaches 64 of 76 id routes. A hand-written test in the same file covers nine of the rest (run search, run trace, agent code, bridge exports, uploads, MCP); the SCIM routes (their own bearer token, tested in `test_scim.py`), annotation items (need a flagged run) and the share conversation (the link's token is the capability) are not in the sweep. The test's output (`pytest -s`) lists what the crawl missed.
- **Web:** the project is still chosen in `queries.ts` (about 58 uses of `PROJECT_ID`), not in a store; about 16 hand-written response types remain (Policies, Setup, CodeHelp differ from the generated ones in which fields are optional); the React Compiler lint rules (`set-state-in-effect`, `refs`, `immutability`) are off in `.oxlintrc.json` because about 25 effects set state by design.
- **Web, wrong-shaped answers:** a page that gets JSON of another shape than it expects (version skew) ends in the crash notice with a Reload hint, not a per-screen message; validating every response against the generated schema would fix that and costs a runtime schema library in the bundle.
- **Database:** the RLS migration takes locks on 35 tables in one transaction (it gives up after 60 s; run it in a quiet moment on a large installation). Verified 2026-10-20: a point query through the four-level policy chain takes about 1 ms at 120,000 runs; re-measure with `EXPLAIN (ANALYZE)` as the application role if a tenant's list pages get slow. Large installations should also check the new `run.events` column (up to 2,000 compact events per run) and the pgvector HNSW indexes against their retention.
- **Code:** duplicated engine, worker and packager code (about 1,000–1,300 lines); `Resources` is still LangChain's object for every adapter.
- **Sandbox:** side channels between sandbox containers on one host (CPU timing) are out of scope; run the sandbox on separate nodes (`sandbox.nodeSelector` in the chart) for tenants that need it. The in-process network block in the child is a guard against mistakes, not a wall (see `docs/agent-canvas-spec/19-threat-model.md`).
- **Tests:** tokens built at import time in older tests expire after five minutes, so a very long suite run could trip late tests; give them 24-hour lifetimes when one does. Several agents once shared one scratch Postgres database and left each other's migration revisions in it: use a database of your own per branch.

## 5. Watch

Dated items from the check of 2026-11-08; recheck them at the dates shown.

- **2026-11-17 to 20:** Microsoft Ignite (Agent Framework ships a breaking change in most minor releases). **2026-11-30 to 12-04:** AWS re:Invent (AgentCore, Strands).
- **2026-11-30:** Claude Sonnet 4.5 retires (in the catalog, W023); OpenAI Agent Builder, the Evals API and `v1/prompts` shut down (the importer and `docs/user-guide/switching.md` are ready).
- **2026-12-01:** Gemini 2.5 Pro and Flash are routed to other models (in the catalog). **2026-12-11:** GPT-5, GPT-5 mini, nano, pro, o3 and o3-pro shut down (GPT-5 mini is in the catalog with its date).
- **2027-01-06:** OpenAI self-serve fine-tuning stops taking new jobs (the fine-tuning export's docs say so). **2027-01-14:** GPT-4o, 4o-mini, 4.1 and 4.1-mini shut down: imported flows naming them will fail.
- **openai-agents next major ("native v1", pre-releases v0.24.0-alpha.6/7 on GitHub only, 2026-11-03/08)** makes the root `agents` package a new runtime and moves today's API to `agents.classic` (`openai-agents[classic]`). Keep `openai-agents==0.24.0` exact (pyagentspec's extra has no upper bound). When it reaches PyPI: switch our imports to `agents.classic` in the runtime, packager, code generator, designs and smoke check; a native port needs a Responses-API endpoint on the model bridge and a new approval pause/resume. The importer already reads both styles.
- **MCP:** releases move to every six months (next in late March 2027); sampling, roots, logging and Dynamic Client Registration stay for compatibility until 2027-07-28. **A2A** spec 1.0.1; `a2a-sdk` 1.3 is stricter on clients.
- **LiteLLM 1.106+** breaks Google ADK tool calls ("No module named 'mcp'"); the pin stays at 1.105.7 (try `litellm[mcp]` with the next bump).
- **Framework pins** move weekly; `tests/test_framework_pins.py` lists every place a pin is written.

## 6. Owner

Release tags v0.1.0–v0.32.x not pushed; delete the merged branches still on GitHub (`claude/project-thread-*` ×5, `claude/sprint-11…13-*`, `claude/trusting-curie-n0i720`, `claude/sandbox-warning`); revoke the token pasted earlier; merge #73, hold #69 (Python 3.15: `google-re2` has no wheel); commit or discard `.sitepkgs.tgz` (80 MB, untracked in the repository root); GitHub Actions has had no runners since 2026-10-08, so the nightly Copilot evaluation (`copilot-evals.yml`) has never run. The agents' worktrees were removed on 2026-11-09.
