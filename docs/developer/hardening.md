# Hardening plan

What is still open after the third deep review (2026-11-10) and the fixes that followed it. That review ran eleven
read-only passes (the newest code; run-time; control-plane security; services; adapters, exports and importers; AI
features; web app; deployment and operations; the data layer; engineering quality and supply chain; launch
readiness), had a second agent try to refute every finding, and checked the product against security and AI
standards and the market. 104 findings survived, from one critical (the "Agent from another framework" block ran
workspace code inside the API in production) to small ones; **all of them are fixed** on `claude/hardening-7`, each
with a test that fails without it, a changelog line, and the suites green on SQLite and Postgres. The earlier
reviews' lists are in git: `git show ad11a56:HARDENING.md` (first) and the commit messages of 2026-11-08 (second;
ids CP-, RT-, W-, I-, P-, OP-, R-).

**How to use this file.** One PR per item; every fix lands with a test that fails without it. In the same PR, delete
the item here and add anything new you find. Items keep short ids so a PR title can name them.

---

## 1. Needs something this environment did not have

| Id | Item | Why it is open | To close it |
|---|---|---|---|
| O-1 | **Run the Copilot evaluation on a hosted model** (G-11) | Done on a local model on 2026-11-09 (LM Studio, Qwen3.6 35B-A3B on two Radeon GPUs): loop 7/10 build and 136/137 debug, Deep Agents 7/10 and 135/137 but 2.7× slower, with more tokens and at its step limit; the loop stays the default ([build-guide/14-copilot-evaluation.md](build-guide/14-copilot-evaluation.md)). The nightly hosted-model job has never run (no GitHub runners) | `make copilot-evals MODEL=anthropic:claude-sonnet-5-5` for each engine when a key and runners exist; the Deep Agents switch waits on branch `pending/copilot-deep-agents-default` |
| O-2 | **Try the two integrations built from reference docs against the real services** | The Claude Managed Agents export (`ant apply` bundle) and the Git round trip (GitHub pull requests) were built from the reference documentation and tested with golden files and a mock transport; nothing here could call Anthropic or GitHub | Apply a bundle with the real `ant` CLI; connect a scratch repository; fix what differs and add it to the golden files |
| O-6 | **A person reading the product with a screen reader** | An automated scan (`e2e/axe.spec.ts`, axe-core, WCAG 2.1 A and AA, light and dark themes, every page and every editor tab) passes, and the inspector, menus and canvas are named and keyboard-reachable (`a11y-inspector.spec.ts`, `keyboard.spec.ts`). A machine cannot say whether the product is usable with NVDA, VoiceOver or JAWS | A screen-reader pass by a person; file what they find |

## 2. Improvement plan (re-planned 2026-11-10 after the launch review)

The third deep review (2026-11-10) fixed 104 verified findings on `claude/hardening-7` (see the changelog and
decisions.md). Its research checked the product against OWASP ASVS 5.0, the OWASP LLM and Agentic Top 10s, NIST AI
600-1, ISO 42001, the EU AI Act and CRA, SOC 2 and EnterpriseReady, and against the market as of 2026-11-10. The
enterprise controls a buyer's security review asks for come first (E), then the re-scoped F items.

### E. Enterprise controls (before F-8 onward)

Built 2026-11-10 on `claude/hardening-8` (first versions, tested on SQLite and Postgres and documented): E-1 KMS
clients in the image; E-2 durable results for deployment callers (start without waiting, read later, cancel, signed
completion callbacks, A2A `GetTask` and cancel on any replica); E-3 audit forwarding through an outbox (retries, dead
letters, Splunk HEC, everything or security only) and a per-organisation hash chain the app role cannot rewrite; E-4
MCP tool pinning at publish; E-5 the AI notice on shared chat and the widget, `generated_by` and `X-AI-Generated`,
`security.txt`; E-6 who may decide an approval, notifications (e-mail, Slack, signed webhook) and escalation; E-7
organisation export and deletion with a grace period, erasing a member or an end user, a person's data export; E-8
several expiring deployment keys, IP allow-lists, required MFA; E-9 organisation and project spending caps. What is
left of them:

| Id | Left | Size |
|---|---|---|
| E-1 | Azure Key Vault and HashiCorp Vault master keys | S |
| E-2 | A2A push notifications and `SubscribeToTask`; callback delivery shown in the Runs view; cancelling a paused task (A2A refuses it, MCP leaves the pause waiting) | S |
| E-3 | A keyed hash (HMAC) column beside the plain chain; syslog/OTLP destinations | S |
| E-4 | Pins enforced in exported code; a per-environment default of "fail" for changed tools; `tool_drift` in the web Deployments panel | S |
| E-5 | `generated_by` inside MCP and A2A response bodies (the header is sent); an audited organisation switch for the notice, if counsel wants one | S |
| E-6 | Deciders from SSO-claim-only groups; retracting notices of withdrawn items (Approve and Deny links, reminders and the Run tab: done 2026-11-10) | S |
| E-7 | Matura's tenant-deleted event wired to the purge; legal hold; crypto-shredding (spec 18's claim is still untrue); exports of very large organisations to object storage | M |
| E-8 | Drop the old `deployment.api_key_hash` and `key_prefix` columns (kept for rolling upgrades); a lifetime cap for deployment keys; `auth_time` freshness for admin writes and an idle-session timeout | S |
| E-9 | Project admins setting their own project's caps (after E-10); stopping a Copilot turn or a judge mid-way at a cap | S |
| E-10 | ~~**Access per project**~~ done 2026-11-10 (project members with roles, private projects, project admins). Left: groups only from the sign-in token's claim; per-project notification channels; custom permission sets | | M |
| E-11 | ~~**OAuth 2.1 for the deployment MCP and A2A endpoints**~~ done 2026-11-10 (RFC 9728 metadata, JWT access tokens, per-person end users). Left: introspection (RFC 7662) and DPoP; who may call by SCIM group (tokens on AG-UI and the REST deployment API: done 2026-11-10) | | M |
| E-12 | ~~**AWS Bedrock and Google Vertex AI providers**~~ done 2026-11-10 (chat models, region per block, IRSA and Workload Identity). Left: their embedding models; a region picker; credential variables in exported deploy kits | | M |
| E-13 | ~~**An AI-system record per deployment**~~ done 2026-11-10 (fields, evidence, Markdown and HTML export). Left: a policy preset requiring owner and risk class in prod; a snapshot at each publish; kill-switch and key history in it | | M |

### F. Product (re-scoped)

| Id | Item | Why | Size |
|---|---|---|---|
| F-1 | ~~**MCP 2026-07-28 for paused and long runs**~~ done 2026-11-10: a pause asks the person through form elicitation (MRTR, a signed single-use `requestState` bound to the key used), long or queued runs become Tasks (`tasks/get`, `tasks/update`, `tasks/cancel`) on any replica. Left: `notifications/tasks`, URL-mode elicitation for reviewer-only pauses | Tasks became a Final extension and MRTR replaced server-initiated elicitation in 2026-07-28 | M |
| F-3b | ~~**LangGraph strict resume**~~ done 2026-11-10 (every resume path names its pause; parallel pauses answer separately). Left: the Agent Spec flow kit; withdrawing on frameworks other than LangGraph (withdrawn items: done 2026-11-10) | | S |
| F-8 | ~~**AG-UI 1.0 endpoint for deployments**~~ done 2026-11-10 (official package, front-end tools, several answers at once). Left: files in user messages; MESSAGES_SNAPSHOT; front-end tools on frameworks other than LangGraph; OAuth on this endpoint | | M |
| F-9 | **Where people work:** actionable approvals in Slack and email first (with E-6); Teams through A2A and Microsoft Agent 365 (Bot Framework is end-of-life) once E-11 lands; a Slack conversational app last | Re-scoped from "publish to Slack, Teams or email", which was three products | M |
| F-10 | ~~**A durable Wait block**~~ done 2026-11-10 (duration, time, event; Stop and the kill switch cancel it). Left: other frameworks; the Run tab following the run a wake starts | | S |
| F-11 | ~~**Copilot `search_runs` and a weekly digest**~~ done 2026-11-10 (Ask box on Runs, digest sent to the notification channels on opt-in). Left: a time zone and send day per organisation; per-project opt-in | | S |
| F-13 | ~~**Stdio MCP servers in the sandbox**~~ done 2026-11-10 (per run or per conversation, a browser image with Playwright MCP). Left: screenshots as run files; more sandboxable gallery entries; keeping a conversation's browser on one server | | M |
| F-14 | **Live co-editing; voice** | N-3 and the audio part of N-4; large, after the rest | L |

Done: F-2 (2026-11-09), F-4, F-5, F-6, F-7 (2026-11-09; F-5's queue redesigned 2026-11-10), F-3's pin bumps
(2026-11-09). F-12 became E-7 and E-10.

## 2a. Left over from the 2026-11-08 fixes


- **Service connection:** a builder can still use the organisation's service connection by publishing and running the deployment (inherent in builders being allowed to publish). A schedule's own input may use any of the project's files.
- **Thread ids** stay one namespace across organisations: whether an id is taken is visible, not by whom (namespacing would change what callers get back).
- **Hosted edition:** only the OpenAI-compatible client is pinned to public addresses; other providers with their own address are refused there. No pinned sync client. The design-export kit (`designs/kit/canvas_tools.py`) still uses plain `is_global` (it cannot import AgentCanvas).
- **Model catalog:** GPT-5.6 and Gemini 3.8 Flash have their published prices (2026-11-09) but no capability flags (vision, sampling) yet. GPT-5.6 Sol's price is promotional until 2026-11-30 and its later price is not published: recheck then. Gemini 3.8 Flash is recorded at its 2027 price (double the introductory one), so budgets err on the safe side until then. `grok-4` has no retire date; Haiku 5.5 cache prices are derived from the stated multipliers.
- **Operations ideas not done:** a startup check that migrations are at head when `AUTO_MIGRATE` is off (done 2026-11-09); a generated reference page of every server setting (done 2026-11-09: `docs/user-guide/server-settings.md`); HSTS and Permissions-Policy headers in the web image (done 2026-11-09).
- **Web:** the Run panel and chat input re-render once per animation frame while tokens stream (the batcher in `run/session.ts`); they show the text, so that is expected.

## 3. Decided not to build (yet), with the reason

| Id | Item | Reason |
|---|---|---|
| N-2 | AG-UI wire for the Copilot, a Copilot checkpointer with time travel, a per-thread `/run` route ([product plan](history/product-plan-2026-10.md)) | Would run beside the SSE format the web already reads, with no gain except CopilotKit interop, which the plan itself rejects. Revisit if interop becomes a goal |
| N-3 | Live co-editing (merging simultaneous edits, cursors) | Presence is built (who is here, which block, "saved a newer version"); merging edits needs a CRDT over the IR with the command log kept as the source of truth |
| N-4 | Interactive multi-turn red teaming (an attacker model reacting to each reply), audio and video ports, files for Planning Agents, file downloads for share visitors | Scope; documented in the guide pages for each |
| N-5 | OAuth connectors in exported code | A workflow exported as code cannot sign people in (the AgentCanvas server holds the connections); the export fails with a clear message. (Hand-entered client credentials and the schedule warning are done.) |
| N-6 | A memory embedding model per project | The LangGraph store's vector column has one size per store, so the model is a server setting (`AGENTCANVAS_MEMORY_EMBEDDING`). Per project would need a store per vector size |
| N-7 | Decrypting secrets per project with a short lifetime instead of one cache per API process | Code that could read the cache could also read the KMS key; no tenant code runs in the API process (the sandbox is required in staging and production). See `decisions.md` |

## 4. Carried over

- **Cross-organisation sweep coverage:** the crawl reaches 64 of 76 id routes. A hand-written test in the same file covers nine of the rest (run search, run trace, agent code, bridge exports, uploads, MCP); the SCIM routes (their own bearer token, tested in `test_scim.py`), annotation items (need a flagged run) and the share conversation (the link's token is the capability) are not in the sweep. The test's output (`pytest -s`) lists what the crawl missed.
- **Web:** the project is still chosen in `queries.ts` (about 58 uses of `PROJECT_ID`), not in a store; response types come from `generated.ts` (output models whose defaulted fields are always sent mark them required with `json_schema_serialization_defaults_required`), with typed adapters where the API answers with open objects (the editor's IR and commands, the Copilot conversation, `/me`'s roles); six remain hand-written because their routes have no response model (event send and dry run, explain, checkpoint history and state) or an untyped one (workspace catalog, trace export), besides small inline ones; the React Compiler lint rules (`set-state-in-effect`, `refs`, `immutability`) are off in `.oxlintrc.json` because about 25 effects set state by design.
- **Web, wrong-shaped answers:** a page that gets JSON of another shape than it expects (version skew) ends in the crash notice with a Reload hint, not a per-screen message; validating every response against the generated schema would fix that and costs a runtime schema library in the bundle.
- **Database:** the RLS migration takes locks on 35 tables in one transaction (it gives up after 60 s; run it in a quiet moment on a large installation). Verified 2026-10-20: a point query through the four-level policy chain takes about 1 ms at 120,000 runs; re-measure with `EXPLAIN (ANALYZE)` as the application role if a tenant's list pages get slow. Large installations should also check the new `run.events` column (up to 2,000 compact events per run) and the pgvector HNSW indexes against their retention.
- **Code:** duplicated engine, worker and packager code (about 1,000–1,300 lines); `Resources` is still LangChain's object for every adapter.
- **Sandbox:** side channels between sandbox containers on one host (CPU timing) are out of scope; run the sandbox on separate nodes (`sandbox.nodeSelector` in the chart) for tenants that need it. The in-process network block in the child is a guard against mistakes, not a wall (see [spec/19-threat-model.md](spec/19-threat-model.md)).
- **Tests:** several agents once shared one scratch Postgres database and left each other's migration revisions in it: use a database of your own per branch. Parallel agents in git worktrees must not use `git stash` (one list for every worktree) and must not commit a symlink to another checkout's `.frameworks`.
- **Spec features never built** (the old progress log's GA backlog, 2026-10-18; pruned 2026-11-10, so check the
  code before starting one): Data Studio (spec 08), learning loops (spec 09, narrowed to the prompt-optimisation
  experiment) and a hosted trial; from spec 15 FR-X-04 generative UI (target A2UI 1.0 and MCP Apps, after F-8),
  FR-X-12 trace-to-canvas import (from OpenTelemetry GenAI spans), -16 multimodal ports (N-4), -20 visual diff in
  PRs and -21 per-user runtime connections. Done meanwhile: FR-X-03 is part of F-8, FR-X-23 (budget-aware routing) is
  built as *Use the cheapest of*. Superseded by standards and not planned: FR-X-06 Engine bridge, FR-X-09 stream
  projections (AG-UI covers them), FR-X-11 ACP exposure (A2A and MCP cover editors). Also the SP-2 follow-up (measure a
  real LangSmith sandbox: [build-guide/spikes/SP-2-langsmith-sandbox.md](build-guide/spikes/SP-2-langsmith-sandbox.md))
  and an agent-sandbox backend widget (`backend.sandbox`, W074).

## 5. Watch

Dated items from the check of 2026-11-08; recheck them at the dates shown.

- **2026-11-17 to 20:** Microsoft Ignite (Agent Framework ships a breaking change in most minor releases). **2026-11-30 to 12-04:** AWS re:Invent (AgentCore, Strands).
- **2026-11-30:** Claude Sonnet 4.5 retires (in the catalog, W023); OpenAI Agent Builder, the Evals API and `v1/prompts` shut down (the importer and `docs/user-guide/switching.md` are ready).
- **2026-12-01:** Gemini 2.5 Pro and Flash are routed to other models (in the catalog). **2026-12-11:** GPT-5, GPT-5 mini, nano, pro, o3 and o3-pro shut down (GPT-5 mini is in the catalog with its date).
- **2027-01-06:** OpenAI self-serve fine-tuning stops taking new jobs (the fine-tuning export's docs say so). **2027-01-14:** GPT-4o, 4o-mini, 4.1 and 4.1-mini shut down: imported flows naming them will fail.
- **openai-agents next major ("native v1", pre-releases v0.24.0-alpha.6/7 on GitHub only, 2026-11-03/08)** makes the root `agents` package a new runtime and moves today's API to `agents.classic` (`openai-agents[classic]`). Keep `openai-agents==0.24.0` exact (pyagentspec's extra has no upper bound). When it reaches PyPI: switch our imports to `agents.classic` in the runtime, packager, code generator, designs and smoke check; a native port needs a Responses-API endpoint on the model bridge and a new approval pause/resume. The importer already reads both styles.
- **MCP:** releases move to every six months (next in late March 2027); sampling, roots, logging and Dynamic Client Registration stay for compatibility until 2027-07-28. Tasks is a Final extension and MRTR replaced server-initiated elicitation in 2026-07-28 (both served since F-1). **A2A** spec 1.0.1; `a2a-sdk` 1.3.0 (2026-10-27) is stricter on clients. **EU AI Act** Art. 50(2) marking grace period ends 2026-12-02 (served since E-5); **CRA** reporting of actively exploited vulnerabilities applies since 2026-09-11 (`.github/SECURITY.md`).
- **LiteLLM 1.106+** breaks Google ADK tool calls ("No module named 'mcp'"); the pin stays at 1.105.7 (try `litellm[mcp]` with the next bump).
- **Framework pins** move weekly; `tests/test_framework_pins.py` lists every place a pin is written.

## 6. Owner

**Licence:** BSL 1.1 is in place (2026-11-11). Have a lawyer review the Parameters before selling licences, and add a private contact address for commercial enquiries if you want one (today it is a GitHub issue). **Switch on GitHub private vulnerability reporting** (Settings › Code security) so `.github/SECURITY.md`'s link works, and confirm its response targets. Release tags v0.1.0–v0.32.x not pushed; delete the merged branches still on GitHub (`claude/project-thread-*` ×5, `claude/sprint-11…13-*`, `claude/trusting-curie-n0i720`, `claude/sandbox-warning`); revoke the token pasted earlier; merge #73, hold #69 (Python 3.15: `google-re2` has no wheel); commit or discard `.sitepkgs.tgz` (80 MB, untracked in the repository root); GitHub Actions has had no runners since 2026-10-08, so the nightly Copilot evaluation (`copilot-evals.yml`) has never run. The agents' worktrees were removed on 2026-11-09.
