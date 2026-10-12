# 16 — Competitive Roadmap (S15–S36)

> **Re-planned 2026-10-09** at the owner's request after the [framework coverage review (doc 17)](17-framework-coverage-review.md):
> framework design and mixed-framework flows move ahead of Ship to users and RAG, and the owner's builder fixes
> become S17. §7 is the current order; §6 keeps the original scoring for reference.
>
> Approved by the owner on 2026-10-07. This order replaces the S15–S19 plan from the Copilot plan v2 and the
> "S20+ GA backlog" row of the [sprint plan](build-guide/13-sprint-plan.md). Live, editable copy:
> [AgentCanvas competitive roadmap](https://claude.ai/code/artifact/1d2f1388-cc14-4200-9681-623e5e065bba) (Claude Doc).

## 1. Summary

After S14 (AI Copilot I, merged in #15), the next three sprints are cheap, high-value wins: make every workflow
callable over MCP and A2A, add integrations through an MCP connector gallery, and let any framework's agent run as a
node on the canvas. That last one gives AgentCanvas working support for every popular open-source agent framework by
S16, before full per-framework authoring lands.

- **Ranking rule:** each candidate gets a value score (1–5, from the market gaps in §3) and an effort in sprints;
  the plan runs the highest value per sprint first, with a +1 bump for multi-framework work because it is the
  product's core promise.
- **Multi-framework in three tiers:** every framework gets Interop in S16; from S20–S22 (re-plan of 2026-10-09)
  the canvas designs single agents in every framework and mixes them in one flow, then the most-used frameworks get
  whole-flow authoring and code export one by one, in order of adoption (§4).
- **Unique feature it unlocks:** compile one workflow to two or more frameworks and compare quality, cost and latency
  side by side in Evaluation Studio (S25). No competitor found offers this.
- Every sprint keeps the standing rule: review the whole codebase for errors first, merge when done, version tags are
  left to the owner.

## 2. Similar products (October 2026)

| Group | Products | Where they beat AgentCanvas | Where AgentCanvas is ahead |
|---|---|---|---|
| Open-source visual builders | Langflow, Flowise, Dify, Sim Studio | Component count, community, Dify's RAG and app publishing | Canvas compiles to idiomatic, exportable LangGraph code; canvas debugger; eval-gated releases |
| Automation with AI agents | n8n, Zapier Agents, Make | 400+ integrations (n8n), business-user reach | Agent-native (Deep Agents, subagents, memory, approvals), code you own |
| Vendor and framework studios | OpenAI AgentKit / Agent Builder, LangSmith Studio and Fleet, CrewAI Studio, Vellum, Rivet, Stack AI | OpenAI Agent Builder also exports code and has evals; CrewAI has mindshare | Any model including local, self-hosted, org governance |
| Enterprise cloud platforms | Microsoft Copilot Studio, Google Vertex AI Agent Builder, Amazon Bedrock AgentCore, Agentforce, watsonx Orchestrate | Ecosystem lock-in, managed runtime, channels | No cloud lock-in; Bedrock AgentCore is a possible deploy target rather than a competitor |

Earlier positioning notes are in [doc 15 §5](15-concept-evolution-and-new-features.md).

## 3. Market gaps

Buyers in 2026 rank governance, quality and observability first, where AgentCanvas is already strong; it is behind on
reach (integrations, interop, end-user surfaces) and on RAG.

| What buyers or users want | Evidence (search summaries) | AgentCanvas today | Gap |
|---|---|---|---|
| Security and governance | 34% name it the top platform factor; 32% name quality the top barrier ([CrewAI survey](https://www.businesswire.com/news/home/20260211693427/en/Agentic-AI-Reaches-Tipping-Point-100-of-Enterprises-Plan-to-Expand-Adoption-in-2026-New-CrewAI-Survey-Finds)) | Orgs, roles, OIDC, audit, CEL policies, Evaluation Studio, release gates | Small: guardrails (PII, prompt injection, moderation) |
| Observability teams trust | Only a third of teams satisfied ([Dynatrace](https://www.dynatrace.com/news/press-release/pulse-of-agentic-ai-2026/)) | Traces, metrics, online evals, drift alerts | Small: export to Langfuse, Phoenix, Datadog |
| Integrations | n8n 400+; Dify and n8n expose and consume MCP ([Dify 1.6](https://dify.ai/blog/v1-6-0-built-in-two-way-mcp-support)) | HTTP and MCP tool nodes, no gallery | Large |
| Framework choice | LangGraph carries most real usage (~68% of agent-framework downloads) while CrewAI, AutoGen, Agno and others hold large communities ([keenable](https://select.keenable.ai/r/open-source-ai-agents-usage-github-share/jfXoGkRU0H2l6G2hKZhPGw)) | LangGraph only; adapter SPI and 10 adapter specs written | Large: the core promise is unbuilt |
| RAG and knowledge bases | Dify's main draw | pgvector memory, no ingestion studio | Large |
| Ship to end users | Dify apps, OpenAI ChatKit, Copilot Studio channels | API-key endpoints, webhooks, cron | Medium: no chat widget or share page |
| Describe-it-and-build-it assistant | Copilot Studio, n8n, Sim Studio | Copilot I shipped in S14 | Closing |
| Fast start | Template galleries everywhere | A few templates, guided build | Medium |

Exact figures are approximate: the pages were not opened (the build environment's proxy blocks most vendor sites).

## 4. Multi-framework strategy

Builds on [doc 11](11-framework-agnostic-plan.md), [doc 12](12-multi-framework-technical-spec.md), the adapter SPI in
`apps/api/agentcanvas/adapters/spi.py` and the [adapter specs](adapters/README.md); it does not redesign them.

**Tiers** (doc 11 §7): **Interop** — the framework's agent runs as a node (Wrapped Agent, remote A2A agent or MCP
agent node), cheap and covers every framework at once · **Authoring** — idiomatic codegen, deploy, tracing, limited
step debugging · **Full** — authoring plus live debugging, approvals, persistence, evals and import.

| Framework | Interop | Authoring or Full | Sprint | Why this order |
|---|---|---|---|---|
| LangChain / LangGraph / Deep Agents | Done | Full, done | — | Most real-world usage |
| OpenAI Agents SDK | S16 | Agent design S21; flows and Full (agent tier) S25 | S21, S25 | Large user base; smallest adapter; unlocks convert-and-compare |
| Pydantic AI and Agno | S16 | Agent design S21; flows Authoring S32 | S21, S32 | Fast-growing, cheap at Authoring tier; Agno is new to the adapter list |
| CrewAI | S16 | Agent design S21; Full for Flows, Authoring for Crews S27 | S21, S27 | Biggest multi-agent community; moved ahead of Microsoft and ADK |
| Google ADK | S16 | Agent design S21; flows Full S29–S30 | S21, S29–S30 | Enterprise reach through Google Cloud |
| Strands and LlamaIndex Workflows | S16 | Agent design S21; flows Authoring S35 | S21, S35 | Smaller share; LlamaIndex pairs with Knowledge Studio |
| Microsoft Agent Framework (AutoGen's successor) | S16 | Agent design S21; flows Authoring, Full later | S21, S33–S34 | Covers AutoGen users and .NET-leaning enterprises |
| Mastra (TypeScript) | S16 via MCP or A2A | Authoring | S36 | Needs a Node.js sidecar host |
| Claude Agent SDK | S16 | Agent design S21 | S21 |
| AG2, smolagents, Semantic Kernel, Haystack, DSPy, Vercel AI SDK, .NET and Java frameworks | S16 | Interop only (wrapped, A2A or MCP) until users ask | — | Wrapped or remote is enough until demand shows |

- **Portable core plus native extras:** core blocks compile to any framework; framework-only blocks are marked, and a
  portability check says which frameworks a workflow can target.
- **Mixed flows (S21, S28):** each agent on the canvas picks its framework; the flow runs in the LangGraph host with
  the other frameworks' agents in process, or as parts with an environment of their own when their packages clash
  with LangGraph's (A2A for other languages), and S28 exports and deploys such a flow as one
  project with typed boundaries between the parts.
- **Full depth only for the top three** (OpenAI Agents SDK, CrewAI, Google ADK); the rest stop at Authoring until
  users ask, because each adapter spec sizes Full at 12–24 team-weeks.
- **Convert and compare:** from S25, one click recompiles a workflow for another framework and Evaluation Studio runs
  both on the same dataset.
- **Same platform services for all:** runs, inbox, triggers, versions, evals, policies and deployments stay
  framework-neutral, so each adapter inherits them.

## 5. Changes to the owner's ideas

1. "Support all frameworks" becomes "every framework runs from S16, the top ones go deep": equal depth for ten
   frameworks would take years and produce a lowest-common-denominator canvas.
2. Multi-framework is a feature, not a checkbox: convert-and-compare uses the Evaluation Studio already built.
3. Integrations come through a curated MCP connector gallery instead of hand-built connectors.
4. Every workflow becomes a tool others can call (MCP and A2A endpoints, old S17, moved to S15).
5. The Copilot is framework-aware (started in S14, #15).
6. The two beginner sprints merge into one (S23), paired with importers from Langflow, Flowise and n8n.

## 6. Value per sprint

Original scoring of 2026-10-07, with the sprint numbers of that plan (see §7 for the current numbers). Score = (value + bump) / effort in sprints. Value scores are judgement calls from §3, not measured data.

| Sprint | Theme | Value | Bump | Effort | Score |
|---|---|---:|---:|---:|---:|
| S15 | Open doors: MCP, A2A, connectors | 5 | 0 | 1 | 5.0 |
| S16 | Any framework as a node | 4 | 1 | 1 | 5.0 |
| S17 | Ship to users | 4.5 | 0 | 1 | 4.5 |
| S18 | Copilot II and debugging | 4 | 0 | 1 | 4.0 |
| S19 | Deploy kit | 4 | 0 | 1 | 4.0 |
| S20 | Knowledge Studio (RAG) | 4 | 0 | 1 | 4.0 |
| S21 | OpenAI Agents SDK | 2.5 | 1 | 1 | 3.5 |
| S22 | Model gateway and cost | 3.5 | 0 | 1 | 3.5 |
| S23 | Onboarding and importers | 3 | 0 | 1 | 3.0 |
| S24 | Pydantic AI and Agno | 2 | 1 | 1 | 3.0 |
| S25–S26 | CrewAI | 4.5 | 1 | 2 | 2.75 |
| S27–S28 | Google ADK | 4 | 1 | 2 | 2.5 |
| S29 | Strands and LlamaIndex | 1.5 | 1 | 1 | 2.5 |
| S30–S31 | Microsoft Agent Framework | 3 | 1 | 2 | 2.0 |
| S32 | Mastra | 1 | 1 | 1 | 2.0 |

Ties are broken by earlier owner decisions (Copilot II and the deploy kit keep their places) and by framework adoption.

## 7. Sprint plan

Current order (re-planned 2026-10-09, [doc 17](17-framework-coverage-review.md)). Release numbers follow merge order;
the ones below assume each sprint merges in this order.

| Sprint | Release | Theme | Scope | Done when |
|---|---|---|---|---|
| S14 | v0.6.0 | AI Copilot I | ✅ merged in #15, including target-framework awareness | — |
| S15 | v0.7.0 | Open doors | ✅ merged in #17. Deployments as MCP tools and A2A agents; MCP connector gallery with about 30 one-click servers; trace export over OpenTelemetry to Langfuse, Phoenix and Datadog | A workflow is callable from Claude Desktop; 30 connectors pass a smoke test |
| S18 | v0.8.0 | AI Copilot II and debug assistant | ✅ merged in #19 | The Copilot tests, debugs and fixes workflows |
| S19 | v0.9.0 | Export deploy kit | ✅ merged in #19 | Exports are deployable codebases with prerequisites, setup and deploy guides |
| S16 | v0.10.0 | Any framework as a node | ✅ merged in #21. Wrapped Agent step for nine frameworks; remote A2A and MCP agent steps; Agent Spec import and export; framework badges | An agent from each of the nine frameworks runs in a canvas workflow with approvals and traces |
| **S17** | v0.11.0 | **Workspace and editor fixes** (owner's findings, 2026-10-09) | ✅ done (S17 PR). A workspace per workflow (plus a shared project library); exports bundle exactly the code the workflow uses; a way back from the Workspace to the workflow; Copilot help in the code editor; every button labelled, starting with the list fields in the AI agent block | A beginner opens a block's code, writes a tool with the Copilot's help, goes back to the canvas, and the export runs with that code; an automated check finds no unlabelled button |
| **S20** | v0.12.0 | **Framework foundation** | ✅ done (S20 PR). Adapter interface v2 (required core plus optional abilities; model calls moved out of the framework adapter); the runtime picks the adapter per workflow; IR v1.2 with an `engine` per node; framework picker in the editor; portability check (P001–P004) and badges; shared conformance kit | A toy second adapter passes the conformance kit; the editor shows what blocks a workflow from each framework |
| **S21** | v0.13.0 | **Design any agent** | ✅ done (S21 PR). The canvas Agent block compiles to a native agent in each of the nine frameworks (instructions, model, tools incl. MCP, HTTP and Python, structured output), chosen per block; mixed flows run in the LangGraph host with approvals and traces; a single agent exports as a native project per framework | A flow with an OpenAI, a CrewAI and an ADK agent, all drawn on the canvas, runs and exports; each agent also exports alone and runs with its framework's own runner |
| **S22** | v0.14.0 | **Whole flows, preview** | ✅ done (S22 PR; LangGraph, WayFlow and OpenAI Agents, see the sprint plan for why not CrewAI or AutoGen). Agent Spec bridge: portable flows export to CrewAI, OpenAI Agents, AutoGen and LangGraph through pyagentspec, tested in CI, marked preview | 5 templates export and run on 3 frameworks |
| **S23** | v0.15.0 | **Ship to users (was S17)** ✅ done | Embeddable chat widget and shareable app page per deployment; guardrails block pack (PII redaction, prompt-injection check, moderation, output schema check); template gallery with 25 use-case templates | A deployment is usable by a non-developer from a link; each guardrail has eval cases |
| **S24** | v0.16.0 | **Knowledge Studio (was S20)** ✅ done (S24 PR; Drive through upload or an MCP agent, search in Python rather than pgvector, see the sprint plan) | Document upload and web or Drive ingestion, chunking options, hybrid search on pgvector, citations, retrieval evals | A RAG template beats a baseline on a retrieval eval set |
| **S25** | v0.17.0 | **OpenAI Agents SDK flows (was S21)** ✅ done (S25 PR; no sessions, handoffs or hosted tools yet, see the sprint plan) | Flow tier and Full agent tier per its adapter spec, on the S20 foundation; convert-and-compare in Evaluation Studio | One template compiles to both frameworks and compares in one experiment |
| **S26** | v0.18.0 | **Model gateway and cost controls (was S22)** ✅ done (S26 PR; the answer cache lives in each server's memory, see the sprint plan) | Fallback models, per-workflow budgets, response caching, cost alerts | A budget stops a run; a provider outage fails over |
| **S27** | v0.19.0 | **CrewAI flows (was S25–S26, moved up)** ✅ done (S27 PR; flows run in a worker with CrewAI in its own environment, see the sprint plan) | Full for Flows, Authoring for Crews; CrewAI-only blocks | Conformance suite passes on the portable set |
| **S28** | v0.20.0 | **Mixed-flow export (new, doc 12 WS7)** ✅ done (S28 PR; frameworks whose packages clash with LangGraph's run as parts in their own environment) | Typed contracts between framework parts; one deployable project holding several frameworks (deploy kit installs and vendors each); A2A for parts in other languages | A mixed template deploys with the deploy kit and passes `make smoke` |
| **S29–S30** | v0.21.0 | **Google ADK flows (was S27–S28)** ✅ done (S29 PR; workflows run as native ADK graph workflows in a worker with ADK in its own environment, tool approvals included, see the sprint plan) | Full | Conformance suite passes |
| **S31** | v0.22.0 | **Onboarding and switching (was S23)** ✅ done (S31 PR; galleries: the template gallery from S23 plus import from Home) | Beginner items (Playground, Simple mode, galleries, Push to GitHub); importers from Langflow, Flowise and n8n JSON | A Langflow export opens and runs on the canvas |
| **S32** | v0.23.0 | **Pydantic AI and Agno flows (was S24)** ✅ done (S32 PR) | Authoring tier | Templates compile and deploy on both |
| **S33–S34** | v0.24.0 | **Microsoft Agent Framework flows (was S30–S31)** ✅ done (S33–S34 PR) | Authoring tier; Full later if users ask | Conformance suite passes on the portable set |
| S35 | v0.25.0 | Strands and LlamaIndex Workflows flows (was S29) | Authoring tier | Templates compile and deploy on both |
| S36 | v0.26.0 | Mastra (was S32) | Authoring tier with a Node.js sidecar host | A TypeScript project exports and runs |

The S17 number now holds the owner's workspace and editor fixes; the old S17 "Ship to users" is S23. Later, if
demand shows: real-time co-editing, two-way Git sync, Data Studio, learning loops, a hosted cloud trial, and
authoring for the long-tail frameworks in §4.

## 8. Risks and open questions

- **Framework churn:** pinned versions per adapter, nightly upstream checks, adapter versions separate from the core.
- **Estimates:** the adapter specs size each adapter for a team over 12–24 weeks; one or two sprints here assume the
  pace of S9–S14 and may stretch.
- **Re-plan cost (2026-10-09):** Ship to users and Knowledge Studio move back six and four sprints to make room for
  S17 and S20–S22.
- **Interop is not authoring:** wrapped agents are black boxes on the canvas (no per-step debugging inside them); docs
  and badges must say so.
- **Open question:** a hosted cloud trial is the biggest adoption lever but means running paid infrastructure. Owner's
  call; not scheduled.

## 9. Sources

Opened: this repository's README, docs 11, 12 and 15, the adapter specs index, `adapters/spi.py`.
Search-result summaries only (pages not opened): the CrewAI, Dynatrace, Dify and keenable links above;
[n8n MCP server](https://dev.to/abhijeet_singh_4577af3ef9/n8n-mcp-server-turn-workflows-into-ai-agent-tools-2026-55eg),
[top agent repositories by stars](https://fungies.io/top-github-repositories-ai-agent-frameworks-2026/),
[open-source builders compared](https://madappgang.com/blog/open-source-visual-agent-builders-compared-flowise-vs-langflow-vs-n8n-vs-sim-studio-in-2026/),
[enterprise platforms 2026](https://wetheflywheel.com/en/guides/enterprise-ai-agent-platforms-2026/).
That AutoGen was folded into Microsoft Agent Framework (AG2 being its community fork) is from prior knowledge.
