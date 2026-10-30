# Hardening plan

What is still open after the deep review of 2026-10-29 and the work that followed it. That review (seven read-only
passes over run-time, control plane, security, web, Copilot and evaluation, adapters and exports, and docs, plus a
check of the standards and the market) produced about 90 findings and a plan of 15 improvements; **all of the
findings and almost all of the plan are done** (165 commits on the hardening branch, each with a test that fails
without it, a changelog line, and the suites green on SQLite and Postgres). Their history is in `CHANGELOG.md`
(*Unreleased*) and `decisions.md`, and the review's own list is in git: `git show ad11a56:HARDENING.md`.

**How to use this file.** One PR per item; every fix lands with a test that fails without it. In the same PR, delete
the item here and add anything new you find. Items keep short ids (`O-1`, `N-2`, …) so a PR title can name them.

---

## 1. Needs something this environment did not have

| Id | Item | Why it is open | To close it |
|---|---|---|---|
| O-1 | **Run the Copilot evaluation on a real model** (G-11) and decide the default engine | The nightly job has never run (no CI runners since 2026-10-08) and no hosted key or local model server was available here. `make copilot-evals` exists and was run offline only. The Deep Agents engine passes every Copilot suite, but making it the default was left out until the scores are known (commit `6423f66` on the agent's branch `feat-copilot`, in the worktree under `.claude/worktrees/agent-a78fb83cd9ffa492c`) | `make copilot-evals MODEL=anthropic:claude-sonnet-5-5` and again with a local model (Ollama): record both scores in `docs/agent-canvas-spec/build-guide/14-copilot-evaluation.md`, then cherry-pick `6423f66` if Deep Agents scores at least as well |
| O-2 | **Try the two integrations built from reference docs against the real services** | The Claude Managed Agents export (`ant apply` bundle) and the Git round trip (GitHub pull requests) were built from the reference documentation and tested with golden files and a mock transport; nothing here could call Anthropic or GitHub | Apply a bundle with the real `ant` CLI; connect a scratch repository; fix what differs and add it to the golden files |
| O-3 | **The `images`, `export` and Kubernetes (kind) stages of local CI** | They build both images, scan them with Trivy, deploy two exports and a kind cluster: heavy, so they run before releases, not on every push. The last full run was before this work | `scripts/ci-local.sh images` and `export` (and `make ci-full`); Trivy may flag the new dependencies (pgvector client, sqlite-vec, pypdf, python-docx) |
| O-4 | **Conformance kit and framework smoke tests on a clean machine** | `tests/test_conformance.py` needs every framework environment, so the full suite here ran without it (`--deselect`). The `frameworks` stage of local CI, which builds those environments and covers it, passed on 2026-10-29 after the pin bumps and before the later merges (files, governance, presence) | Run `make ci-full` on a machine with the Docker images |
| O-6 | **A person reading the product with a screen reader** | The inspector, the menus and the canvas are named and keyboard-reachable (`a11y-inspector.spec.ts`, `keyboard.spec.ts`), but only a person can say whether it is usable | A screen-reader pass; file what they find |

## 2. Decided not to build (yet), with the reason

| Id | Item | Reason |
|---|---|---|
| N-2 | AG-UI wire for the Copilot, a Copilot checkpointer with time travel, a per-thread `/run` route (PLAN.md) | Would run beside the SSE format the web already reads, with no gain except CopilotKit interop, which the plan itself rejects. Revisit if interop becomes a goal |
| N-3 | Live co-editing (merging simultaneous edits, cursors) | Presence is built (who is here, which block, "saved a newer version"); merging edits needs a CRDT over the IR with the command log kept as the source of truth |
| N-4 | Multi-turn red teaming, audio and video ports, files for Planning Agents, file upload by deployment-key callers (they send inline `data`), file downloads for share visitors | Scope; documented in the guide pages for each |
| N-5 | OAuth connectors in exported code | A workflow exported as code cannot sign people in (the AgentCanvas server holds the connections); the export fails with a clear message. (Hand-entered client credentials and the schedule warning are done.) |
| N-6 | A memory embedding model per project | The LangGraph store's vector column has one size per store, so the model is a server setting (`AGENTCANVAS_MEMORY_EMBEDDING`). Per project would need a store per vector size |
| N-7 | Decrypting secrets per project with a short lifetime instead of one cache per API process | Code that could read the cache could also read the KMS key; no tenant code runs in the API process (the sandbox is required in staging and production). See `decisions.md` |

## 3. Carried over from before the review

- **Cross-organisation sweep coverage:** the crawl reaches 64 of 76 id routes. A hand-written test in the same file covers nine of the rest (run search, run trace, agent code, bridge exports, uploads, MCP); the SCIM routes (their own bearer token, tested in `test_scim.py`), annotation items (need a flagged run) and the share conversation (the link's token is the capability) are not in the sweep. The test's output (`pytest -s`) lists what the crawl missed.
- **Web:** the project is still chosen in `queries.ts` (about 58 uses of `PROJECT_ID`), not in a store; about 16 hand-written response types remain (Policies, Setup, CodeHelp differ from the generated ones in which fields are optional); the React Compiler lint rules (`set-state-in-effect`, `refs`, `immutability`) are off in `.oxlintrc.json` because about 25 effects set state by design.
- **Web, wrong-shaped answers:** a page that gets JSON of another shape than it expects (version skew) ends in the crash notice with a Reload hint, not a per-screen message; validating every response against the generated schema would fix that and costs a runtime schema library in the bundle.
- **Database:** the RLS migration takes locks on 35 tables in one transaction (it gives up after 60 s; run it in a quiet moment on a large installation). Verified 2026-10-20: a point query through the four-level policy chain takes about 1 ms at 120,000 runs; re-measure with `EXPLAIN (ANALYZE)` as the application role if a tenant's list pages get slow. Large installations should also check the new `run.events` column (up to 2,000 compact events per run) and the pgvector HNSW indexes against their retention.
- **Code:** duplicated engine, worker and packager code (about 1,000–1,300 lines); `Resources` is still LangChain's object for every adapter.
- **Sandbox:** side channels between sandbox containers on one host (CPU timing) are out of scope; run the sandbox on separate nodes (`sandbox.nodeSelector` in the chart) for tenants that need it. The in-process network block in the child is a guard against mistakes, not a wall (see `docs/agent-canvas-spec/19-threat-model.md`).
- **Tests:** tokens built at import time in older tests expire after five minutes, so a very long suite run could trip late tests; give them 24-hour lifetimes when one does. Several agents once shared one scratch Postgres database and left each other's migration revisions in it: use a database of your own per branch.

## 4. Watch

Dated items from the market and standards check of 2026-10-29; recheck them at the dates shown.

- **OpenAI Agent Builder, Evals and `v1/prompts` shut down 2026-11-30** (Evals read-only from 2026-10-31). The Agents SDK importer and the migration page are done (`docs/user-guide/switching.md`); recheck that OpenAI's export still matches what the importer reads.
- **Claude Sonnet 4.5 retires 2026-11-30** (the catalog carries the date and the editor warns, W023); `gpt-5-mini` and others are reported to follow on 2026-12-11 (confirm on OpenAI's deprecations page and add the `retires` date).
- **Flowise** is archived; its importer's fixtures follow the last release. **MCP** deprecates sampling, roots, logging and Dynamic Client Registration (kept for compatibility for twelve months from 2026-07-28). **A2A** 1.0 patch releases keep the extension mechanism moving.
- **LiteLLM 1.106.x** breaks Google ADK tool calls ("No module named 'mcp'"); the pin stays at 1.105.7 until a release declares `mcp` (see `decisions.md`).
- **Framework pins** move weekly; `tests/test_framework_pins.py` lists every place a pin is written.

## 5. Owner

Release tags v0.1.0–v0.32.x not pushed; delete the merged branches still on GitHub (`claude/project-thread-*` ×5, `claude/sprint-11…13-*`, `claude/trusting-curie-n0i720`, `claude/sandbox-warning`); revoke the token pasted earlier; merge #73, hold #69 (Python 3.15: `google-re2` has no wheel); commit or discard `.sitepkgs.tgz` (80 MB, untracked in the repository root); GitHub Actions has had no runners since 2026-10-08, so the nightly Copilot evaluation (`copilot-evals.yml`) has never run. After merging: the agents' worktrees under `.claude/worktrees/` can be removed (`git worktree prune`; they are ignored by git).
