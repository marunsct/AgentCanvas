---
name: build-for-pydantic-ai-agno
description: Build or move a workflow for Pydantic AI or Agno, or design an agent as one of them. Use when the person names Pydantic AI, Agno, or asks for typed agents in either.
---

# Building for Pydantic AI and Agno

Both run the workflow as an async function per block under AgentCanvas's flow engine, with the framework's own agents
for AI blocks. Pydantic AI: OpenAIChatModel, deferred tools for approval, NativeOutput for typed answers. Agno:
OpenAILike models, `requires_confirmation`, `output_schema`. Pick by what the person names; their support differs in
the facts below.

Before proposing the move, call `export_plan` for this framework and tell the person what it lists. Say what the
framework will not do; promise nothing the facts below do not give.

<!-- support: filled in from the adapter manifests -->
