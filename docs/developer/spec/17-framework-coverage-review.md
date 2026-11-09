# 17 — Framework coverage review (2026-10-09)

> **Adopted by the owner on 2026-10-09** ("update the sprint plan as per the framework coverage review"). The plan
> changes in §5 are now the sprint order in [doc 16 §7](../history/16-competitive-roadmap.md#7-sprint-plan): F1 became **S20
> Framework foundation**, F2 **S21 Design any agent**, F3 **S22 Whole flows, preview**; mixed-flow export is **S28**.
> Sprint numbers below in §5 are the ones this review proposed before renumbering.

*2026-10-09 · reviewed `main` at v0.10.0 (merge `25be73e`, after S16) and the approved plan in
`docs/agent-canvas-spec/16-competitive-roadmap.md`, `build-guide/13-sprint-plan.md`, `HANDOVER.md`, docs 11 and 12
and the adapter specs. Review only; no code changed.*

## 1. Short answer

- **It is not tied to LangSmith.** LangSmith is optional (tracing and dataset sync). The canvas designs, runs and
  exports flows on **LangGraph / LangChain** as its one engine.
- **Today the canvas can only *design* LangGraph flows.** Agents from nine other frameworks (OpenAI Agents SDK,
  CrewAI, Google ADK, Pydantic AI, Agno, Strands, LlamaIndex, Microsoft Agent Framework, Claude Agent SDK) can be
  *used* as steps, but someone has to write those agents in code first; the canvas runs them as black boxes.
- **"Mixed frameworks in one flow" works only in that limited sense:** a LangGraph flow that calls finished agents
  from other frameworks. You cannot draw an agent on the canvas and say "build this one in CrewAI".
- **Against your expectation the plan scores about 55–60%.** Real per-framework authoring is planned, but late
  (first one in S21, last in S32), the *mixed* part is missing from the sprint list entirely, and two foundation
  pieces it depends on are not scheduled.
- **It is feasible**, if "every framework" means *design and export single agents and simple flows in every
  framework, full depth in the top three*. Full parity for ten frameworks is not realistic.
- **Recommendation:** move framework work forward with three new sprints right after S16, before S17 Ship to users
  (§5), so that by roughly S19 you can design an agent on the canvas, pick its framework per node, run a mixed flow
  and export it.

## 2. What exists today (checked in code)

| Capability | Status | Evidence |
|---|---|---|
| Design a flow visually | ✅ LangGraph only | Only adapter installed: `adapters/__init__.py` raises `no adapter installed for framework '…'` for anything but `langchain` |
| Pick a framework for a workflow | ❌ | IR has `workflow.framework = "langchain"` (`ir/models.py:234`) but no UI picker and no second value accepted |
| Pick a framework per node (mixed design) | ❌ | No `engine` field in the IR; doc 12 WS7 describes it but nothing is built |
| Export code | ✅ LangGraph project only (with S19 deploy kit) | `get_adapter(wf.framework).package(...)` in `api/v1/versions.py:177` |
| Run on the platform (runs, debugger, inbox, approvals, memory, time travel) | ✅ LangGraph only | `api/main.py` binds the `langchain` adapter for the whole process; the SPI (`adapters/spi.py`) assumes LangGraph checkpoints, `update_state`, thread copy and a LangGraph store |
| Use an agent built in another framework | ✅ black box, 9 frameworks | `agent.framework` node: `import_path` to code in the Workspace (`adapters/langchain/interop.py:223`); runs inside the LangGraph host; one approval before it runs, no step-by-step view inside |
| Use a remote agent | ✅ | A2A node (0.3 methods, no streaming) and MCP agent node |
| Open Agent Spec import/export | ✅ JSON | `agentspec.py`: exports a workflow as an Agent Spec Flow; imports any Agent Spec flow/agent. Other Agent Spec runtimes can load the export, but nothing here runs or tests it on CrewAI/OpenAI/AutoGen |
| Portable vs framework-only nodes | ⚠️ half | Node registry already splits `core.*` (16 portable nodes) from LangChain-only nodes (`agent.deep`, 10 `mw.*`, `backend.*`), but there is **no portability check** (`P001–P004` from doc 12 are not implemented) |
| Copilot builds for another framework | ❌ | It is "framework-aware" in prompts, but it can only build what the canvas can compile, so LangGraph |

So the honest one-liner for users today: *"Build in LangGraph; bring agents from anywhere."*

## 3. How the plan matches the expectation

Your expectation has four parts. Scored against the approved roadmap S17–S32:

| Expectation | Planned? | When | Score |
|---|---|---|---|
| A. Use agents made elsewhere | Done in S16 (black box, A2A, MCP) | done | 90% |
| B. Design flows in another framework and export its code | Yes: OpenAI (S21), Pydantic AI+Agno (S24), CrewAI (S25–26), ADK (S27–28), Strands+LlamaIndex (S29), Microsoft (S30–31), Mastra (S32) | 5 to 16 sprints away | 60% (right content, too late, single-framework per workflow) |
| C. Mix frameworks in one designed flow | **Not in the sprint list.** Doc 12 has it (WS7, per-node `engine`, boundary contracts), the roadmap dropped it | never scheduled | 15% (only the black-box form from S16) |
| D. Widely used frameworks covered | 10 Python + Mastra | — | 75% (missing: AG2/AutoGen classic, Semantic Kernel, smolagents, Haystack, DSPy, Vercel AI SDK; .NET and Java not at all) |

Weighted roughly equally: **about 55–60%.**

## 4. Feasibility and gaps

**Feasible:**
- **Single agents in any framework are cheap.** Every framework has the same agent shape: instructions, model, tools,
  structured output. The canvas Agent node already holds exactly that. Generating `Agent(...)` for each framework is
  a template per framework, not a new engine. This is the fastest way to "design in any framework".
- **Mixed flows are architecturally ready.** S16 already runs foreign agents inside the LangGraph host and keeps
  approvals, traces and the inbox working. A canvas-designed agent compiled to CrewAI can use the same mechanism
  instead of hand-written code.
- **The Agent Spec bridge** (doc 12 M1.4: load portable flows with `pyagentspec` into LangGraph, CrewAI, OpenAI
  Agents, AutoGen, WayFlow) can give a "preview" export of whole flows for several frameworks at once. It is in
  doc 12 but missing from the roadmap.

**Gaps that will block the planned adapter sprints:**
1. **SPI is LangGraph-shaped.** It requires checkpoint history, `update_state`, thread copy and a key-value store
   from every adapter, and it also carries the Copilot's and the LLM judge's model calls. A second adapter can't
   implement that honestly. Needs an SPI v2: required core (generate, package, execute, events) plus optional
   capabilities (state, time travel, memory), with model calls moved out of the framework adapter.
2. **One adapter per process.** `api/main.py` binds `langchain` at startup; runs, state and memory never look at
   `workflow.framework`. The runtime must choose the adapter per workflow.
3. **No framework picker, no portability check, no per-node engine** in the IR or UI.
4. **Effort estimates are optimistic.** The adapter specs size each adapter at 12–24 weeks for a three-person team;
   the roadmap gives each one or two sprints. Sprint pace so far has been fast, but "Full" tier (live debugging,
   approvals, persistence, import) for five frameworks will stretch. Plan "Authoring" first for all, "Full" only
   for the top three.
5. **Export is one framework per project.** Mixed export (a LangGraph host plus CrewAI and OpenAI agent modules in
   one deployable project) needs the deploy kit to install several framework packages and vendor several runtimes.
   The S16 export already does this for black-box agents, so the gap is small.
6. **Framework-only nodes are LangChain-only today.** Each adapter needs its own dialect nodes (CrewAI crew, ADK
   loop agent, OpenAI handoff and guardrail) or users of those frameworks will find the canvas shallow.
7. **Wrapped agents need hand-written code.** That is fine for "bring your own agent", but it is the opposite of
   "design on the canvas", which is the core of your ask.

## 5. Recommended plan changes

Keep the existing sprint content; insert three framework sprints now and add mixed-flow work to the list.

| New order | Sprint | Scope | Done when |
|---|---|---|---|
| next | **F1 Framework foundation** (new) | SPI v2 (required core + optional capabilities; model calls out of the adapter); runtime picks the adapter per workflow; IR v1.2 with `engine` per node; framework picker in the editor; portability check and badges (which nodes can compile to which framework) | A toy second adapter passes the conformance kit; the editor shows what blocks a workflow from targeting each framework |
| then | **F2 Design any agent** (new) | The canvas Agent node compiles to a native agent in each of the 9 frameworks (instructions, model, tools including MCP/HTTP/Python, structured output); chosen per node; runs as a step in the LangGraph flow with approvals and traces; export of a single agent as a native project per framework | A flow with one OpenAI, one CrewAI and one ADK agent, all drawn on the canvas, runs and exports; each framework's agent also exports alone and runs with that framework's own runner |
| then | **F3 Whole flows, preview** (new) | Agent Spec bridge: export portable flows to CrewAI, OpenAI Agents, AutoGen and LangGraph via `pyagentspec`, tested in CI; marked "preview" | 5 templates export and run on 3 frameworks |
| | S17 Ship to users | unchanged | |
| | S20 Knowledge Studio | unchanged | |
| | S21 OpenAI Agents SDK | flow tier + convert-and-compare, now on the F1 foundation | |
| | S22 Model gateway | unchanged | |
| | **S23 CrewAI** (moved up from S25–26) | Flows authoring + dialect nodes; Full only for Flows | Bigger community than Pydantic AI and Agno |
| | **S24 Mixed-flow export and boundaries** (new, from doc 12 WS7) | Typed contracts between framework parts, one deployable project with several frameworks, A2A for cross-language parts | A mixed template deploys with the deploy kit |
| | S25–26 Google ADK, S27 Onboarding and importers, S28 Pydantic AI + Agno, S29–30 Microsoft Agent Framework, S31 Strands + LlamaIndex, S32 Mastra | Authoring tier first; Full only for OpenAI, CrewAI and ADK | |
| later | Long tail via Interop only | AG2, smolagents, Semantic Kernel, Haystack, DSPy, Vercel AI SDK, .NET and Java frameworks | Black box / A2A until users ask |

Why this order: it delivers the core promise (design in any framework, mix them) about eight sprints earlier than
today's plan, it builds on S16 code that already exists, and it makes every later adapter sprint cheaper because
F1 removes the LangGraph assumptions once. The cost is that S17 Ship to users and S20 Knowledge Studio move back by
three sprints.

Other plan fixes:
- Rename the tiers in the docs and UI so users understand them: "Bring your agent" (Interop), "Design in X"
  (Authoring), "Debug in X" (Full).
- Update the README and user guide to say plainly what works today: "Build in LangGraph, bring agents from anywhere"
  until F2 lands.
- Add one conformance suite shared by all adapters (doc 12 M0.5) as part of F1, so each later adapter sprint has a
  clear "done".

## 6. Decision for the owner

1. **Adopt the F1–F3 insert before S17** (recommended): framework design and mixed flows arrive next, S17 and S20
   wait three sprints.
2. **Keep the approved order, add F1 and mixed flows only:** cheaper change, but designing in other frameworks still
   starts at S21.
3. **Keep the plan as is:** LangGraph-only design until S21, no mixed design at all.
