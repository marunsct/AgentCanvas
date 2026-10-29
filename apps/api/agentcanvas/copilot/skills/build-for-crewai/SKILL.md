---
name: build-for-crewai
description: Build or move a workflow for CrewAI, or design an agent as a CrewAI agent (flows, agents, crews and tasks). Use when the person names CrewAI, a crew, or a team of agents with tasks.
---

# Building for CrewAI

The workflow becomes a CrewAI flow (`@start`, `@listen`, `@router`). Use `crewai.crew` and `crewai.task` blocks for a
team of agents with tasks (`get_template` editorial_crew shows one) and `core.agent` for a single agent. Approvals and
questions stop the flow with CrewAI's human feedback and continue when a person answers.

Before proposing the move, call `export_plan` for this framework and tell the person what it lists. Say what the
framework will not do; promise nothing the facts below do not give.

<!-- support: filled in from the adapter manifests -->
