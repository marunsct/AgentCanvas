# 16 — Competitive Roadmap (S15–S32)

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
- **Multi-framework in three tiers:** every framework gets Interop in S16; the most-used frameworks then get full
  canvas authoring and code export one by one, in order of adoption (§4).
- **Unique feature it unlocks:** compile one workflow to two or more frameworks and compare quality, cost and latency
  side by side in Evaluation Studio (S21). No competitor found offers this.
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
| OpenAI Agents SDK | S16 | Full (agent tier) | S21 | Large user base; smallest adapter; unlocks convert-and-compare |
| Pydantic AI and Agno | S16 | Authoring | S24 | Fast-growing, cheap at Authoring tier; Agno is new to the adapter list |
| CrewAI | S16 | Full for Flows, Authoring for Crews | S25–S26 | Biggest multi-agent community; moved ahead of Microsoft and ADK |
| Google ADK | S16 | Full | S27–S28 | Enterprise reach through Google Cloud |
| Strands and LlamaIndex Workflows | S16 | Authoring | S29 | Smaller share; LlamaIndex pairs with Knowledge Studio |
| Microsoft Agent Framework (AutoGen's successor) | S16 | Full | S30–S31 | Covers AutoGen users and .NET-leaning enterprises |
| Mastra (TypeScript) | S16 via MCP or A2A | Authoring | S32 | Needs a Node.js sidecar host |
| Claude Agent SDK, AG2, others | S16 | Interop only | S16 | Wrapped or remote is enough until demand shows |

- **Portable core plus native extras:** core blocks compile to any framework; framework-only blocks are marked, and a
  portability check says which frameworks a workflow can target.
- **Convert and compare:** from S21, one click recompiles a workflow for another framework and Evaluation Studio runs
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

Score = (value + bump) / effort in sprints. Value scores are judgement calls from §3, not measured data.

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

| Sprint | Release | Theme | Scope | Done when |
|---|---|---|---|---|
| S14 | v0.6.0 | AI Copilot I | ✅ merged in #15, including target-framework awareness | — |
| S15 | v0.7.0 | Open doors | ✅ merged in #17. Deployments as MCP tools and A2A agents (old S17, essentials); MCP connector gallery with about 30 one-click servers (Slack, GitHub, Google Drive, Postgres, browser, search); trace export over OpenTelemetry to Langfuse, Phoenix and Datadog | A workflow is callable from Claude Desktop; 30 connectors pass a smoke test |
| S16 | v0.10.0 | Any framework as a node | Wrapped Agent node with generated wrappers for OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands, LlamaIndex, Microsoft Agent Framework and Claude Agent SDK; remote A2A agent node; MCP agent node; Agent Spec import and export; framework badge on each node | An agent from each of the nine frameworks runs in a canvas workflow with approvals and traces |
| S17 | v0.11.0 | Ship to users | Embeddable chat widget and shareable app page per deployment; guardrails block pack (PII redaction, prompt-injection check, moderation, output schema check); template gallery with 25 use-case templates | A deployment is usable by a non-developer from a link; each guardrail has eval cases |
| S18 | v0.8.0 | AI Copilot II and debug assistant | ✅ merged in #19, ahead of S16–S17 (its draft predated the reorder). Old S15 scope: configure, test, debug and fix | The Copilot tests, debugs and fixes workflows |
| S19 | v0.9.0 | Export deploy kit | ✅ merged in #19, ahead of S16–S17. Old S16 scope: environment-aware export, four guides, `make doctor`/`make smoke` | Exports are deployable codebases with prerequisites, setup and deploy guides |
| S20 | v0.12.0 | Knowledge Studio | Document upload and web or Drive ingestion, chunking options, hybrid search on pgvector, citations, retrieval evals | A RAG template beats a baseline on a retrieval eval set |
| S21 | v0.13.0 | OpenAI Agents SDK adapter | Full agent tier per its adapter spec; portability check; convert-and-compare in Evaluation Studio | One template compiles to both frameworks and compares in one experiment |
| S22 | v0.14.0 | Model gateway and cost controls | Fallback models, per-workflow budgets, response caching, cost alerts | A budget stops a run; a provider outage fails over |
| S23 | v0.15.0 | Onboarding and switching | Old S18–S19 beginner items merged (Playground, Simple mode, galleries, Push to GitHub); importers from Langflow, Flowise and n8n JSON | A Langflow export opens and runs on the canvas |
| S24 | v0.16.0 | Pydantic AI and Agno | Authoring tier | Templates compile and deploy on both |
| S25–S26 | v0.17.0 | CrewAI adapter | Full for Flows, Authoring for Crews | Conformance suite passes on the portable set |
| S27–S28 | v0.18.0 | Google ADK adapter | Full | Conformance suite passes |
| S29 | v0.19.0 | Strands and LlamaIndex Workflows | Authoring tier | Templates compile and deploy on both |
| S30–S31 | v0.20.0 | Microsoft Agent Framework | Full | Conformance suite passes |
| S32 | v0.21.0 | Mastra | Authoring tier with a Node.js sidecar host | A TypeScript project exports and runs |

Later, if demand shows: real-time co-editing, two-way Git sync, Data Studio, learning loops, and a hosted cloud trial.

## 8. Risks and open questions

- **Framework churn:** pinned versions per adapter, nightly upstream checks, adapter versions separate from the core.
- **Estimates:** the adapter specs size each adapter for a team over 12–24 weeks; one or two sprints here assume the
  pace of S9–S14 and may stretch.
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
