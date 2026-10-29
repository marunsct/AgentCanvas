---
name: build-for-strands-llamaindex
description: Build or move a workflow for Strands Agents or LlamaIndex, or design an agent as one of them. Use when the person names Strands (AWS) or LlamaIndex.
---

# Building for Strands Agents and LlamaIndex

Both run the workflow as an async function per block under AgentCanvas's flow engine. AI blocks are Strands Agents
(`structured_output_model`; approvals through a hook using Strands' interrupts) or LlamaIndex FunctionAgents
(`output_cls`; `InputRequiredEvent` for approvals). Pick by what the person names.

Before proposing the move, call `export_plan` for this framework and tell the person what it lists. Say what the
framework will not do; promise nothing the facts below do not give.

<!-- support: filled in from the adapter manifests -->
