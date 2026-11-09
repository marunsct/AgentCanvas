---
name: build-for-openai-agents
description: Build or move a workflow for the OpenAI Agents SDK, or design an agent as one (which blocks it runs, emulates or refuses). Use when the person names the OpenAI Agents SDK or asks for an OpenAI-style agent export.
---

# Building for the OpenAI Agents SDK

AI blocks become SDK Agents (tools, approvals, structured output); every other block runs in a small Python flow
around them. Any catalog model works, not only OpenAI's. Exports are Python projects with a command-line runner; a
paused run lives in memory.

Before proposing the move, call `export_plan` for this framework and tell the person what it lists. Say what the
framework will not do; promise nothing the facts below do not give.

<!-- support: filled in from the adapter manifests -->
