# Open Agent Spec — Interchange & Bridge Path

## 1. Verified baseline
- `pyagentspec` **26.3.1** (main: 26.4.0.dev0), `tsagentspec`; Apache-2.0 / UPL.
- Verified components: `Agent(llm_config, system_prompt, tools, toolboxes, human_in_the_loop, transforms)`; `Flow` with nodes `StartNode, EndNode, LlmNode, AgentNode, ToolNode, ApiNode, BranchingNode, MapNode, ParallelMapNode, ParallelFlowNode, FlowNode, CatchExceptionNode, InputMessageNode, OutputMessageNode`; `ControlFlowEdge`, `DataFlowEdge`; `ManagerWorkers`, `Swarm`, `RemoteAgent`, `A2AAgent`; MCP; datastores (Postgres, Oracle); tracing events (agent, flow, node, tool, llm generation, human-in-the-loop, manager-workers, swarm, state, exception).
- Adapters (loader **and** exporter): `langgraph`, `openaiagents`, `agent_framework`, `crewai`, `autogen`, `wayflow`. Some converters raise `NotImplementedError` for unsupported features (e.g. certain LLM providers or tool types).

## 2. Uses in AgentCanvas

| Use | Description | Phase |
|-----|-------------|-------|
| **Interchange** | Import/export Core IR ↔ Agent Spec (JSON/YAML) so workflows move between AgentCanvas and other Agent Spec tools/runtimes | F1 |
| **Bridge path** | For frameworks without a direct adapter yet: IR → Agent Spec → pyagentspec loader → runtime objects for dev runs & comparisons (T2-preview) | F1 |
| **Cross-framework evaluation** | Use Agent Spec's cross-runtime evaluation approach alongside LangSmith experiments for "same workflow, different framework" comparisons | F4 |
| **Importer fallback** | Framework code → pyagentspec exporter → Agent Spec → IR, when no native importer exists | F2+ |

## 3. Core IR ↔ Agent Spec mapping

| Core IR | Agent Spec |
|---------|-----------|
| `core.agent` | `Agent` (tools/toolboxes; `human_in_the_loop`) |
| Workflow | `Flow` |
| Model node / script | `LlmNode` / `ToolNode` (server/client/remote tools) |
| Agent node in workflow | `AgentNode` |
| HTTP tool node | `ApiNode` |
| Router | `BranchingNode` |
| Map | `MapNode` / `ParallelMapNode` |
| Parallel branches | `ParallelFlowNode` |
| Subgraph | `FlowNode` |
| Error route | `CatchExceptionNode` |
| Input/approval | `InputMessageNode` / agent `human_in_the_loop` |
| Flow edges / data edges | `ControlFlowEdge` / `DataFlowEdge` |
| Supervisor / swarm patterns | `ManagerWorkers` / `Swarm` |
| Remote/A2A agent | `RemoteAgent` / `A2AAgent` |
| Not representable (dialect nodes, middleware, persistence bindings, Data Studio) | carried in `metadata` extension blocks; lossy on export (reported) |

## 4. Plan (F1, part of interop squad, ~6 weeks)
Exporter & importer with golden round-trip tests on 10 portable templates (1–4); bridge execution for OpenAI Agents/MAF/CrewAI dev runs (3–6); gap report surfaced as `P002`/`P004` diagnostics.
