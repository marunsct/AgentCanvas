# 0018 — Adapter interface v2: a required core and optional abilities

Status: accepted · Date: 2026-10-09 (Sprint 20)

## Context
Until S20 there was one `FrameworkAdapter` protocol with every method the LangChain adapter happened to have:
thread state, time travel, long-term memory, MCP browsing, and the model calls of the Copilot and LLM judges. A
second framework would have had to fake all of it, the runtime always used the LangChain adapter whatever the
workflow's `framework` said, and nothing told the editor which blocks a framework can run. The framework coverage
review (doc 17) made "design flows in any framework" the next goal, so the seams had to be real first.

## Decision
- **Required core** (`adapters/spi.py` `FrameworkAdapter`): `id`, `manifest`, `generate`, `package`, `persistence`,
  `execute` (canonical AG-UI events ending in `RUN_FINISHED` or `RUN_ERROR`), `script`, `default_resources`,
  `new_resources`.
- **Optional abilities**, declared in the adapter's manifest and asked for with `require(adapter, ability, Protocol)`:
  `thread_state` and `time_travel` (`ThreadStateAbility`), `memory_store` (`MemoryAbility`), `mcp_inspect`
  (`McpAbility`) and `breakpoints`. Missing ones are refused with a 409 that names what is missing and how to switch.
- **Manifests are data** (`registry/adapters/<id>.json`): label, tier, abilities, and per block type `native`,
  `emulated` or `unsupported` (with `"<namespace>.*"` and `"*"` fallbacks). The validator (P001-P004) and the editor
  read them without importing a framework, so the core stays framework-neutral (ADR 0011).
- **Adapter per workflow**: the runner keeps every installed adapter and picks `workflow.framework`'s for runs,
  thread state, history, code view and export. `AGENTCANVAS_ADAPTERS` says which are installed (default
  `langchain`). The host adapter (LangChain) still holds long-term memory, MCP browsing and model resolution.
- **Model calls move out**: the Copilot, LLM judges and the setup test call a framework-neutral `ModelService`
  (`complete`, `chat`); its implementation uses LangChain's provider integrations today.
- **IR 1.2**: an optional `engine` on AI blocks (`core.llm`, `core.agent`, `agent.deep`) records the framework a block
  is designed for. It is left out of the JSON when unset. Mixed-framework runs come later; until then the block runs
  on the workflow's framework (P004).
- **Conformance kit** (`agentcanvas/conformance`): flow, agent, approval and event cases every adapter must pass;
  the Echo demo adapter passes it next to LangChain, which proves the seams.

## Consequences
+ A new framework implements the core, declares what else it can do, and is checked by the kit; the editor shows
  where each workflow can run. + The Copilot and judges work the same whatever framework a workflow targets.
− Resources (model and tool resolution) are still LangChain's objects passed to every adapter; S21 gives adapters
their own. − Echo keeps paused runs in memory; it is a demo, off by default.
