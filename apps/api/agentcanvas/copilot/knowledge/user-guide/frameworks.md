# Choosing a framework

Every workflow is designed for one **framework**: the agent framework that runs it and that its export is written
for. Today that is **LangGraph** (LangChain's graph runtime), which runs every block in the palette. More frameworks
arrive in the next releases; the editor already shows which ones a workflow could move to.

## Pick the workflow's framework

Click empty canvas to open the workflow settings, then choose **Framework**. The list shows the frameworks this
server offers, with a line on what each one does. Changing it is an ordinary edit: **Undo** brings the old one back.

## See where a workflow can run

Next to the workflow's name, one badge per installed framework says whether it can run this workflow:

- **✓ LangGraph** — every block can run there.
- **✕ Echo · 2** — two blocks cannot run there. Point at the badge to see which; click it to select the first one.

The Problems tab explains the rest:

| Code | Meaning |
|---|---|
| P001 | The workflow's framework cannot run this block, or the framework is not installed on this server |
| P002 | The framework only imitates this block, so it may behave a little differently (the message says how) |
| P003 | The workflow could move to some installed frameworks but not others |
| P004 | This AI block is designed for another framework than the workflow's; for now it runs on the workflow's |

## An AI block of its own framework

An AI step, AI agent or Planning Agent can be **Designed for** another framework than the workflow (in the block's
settings). Until mixed-framework flows arrive, the block still runs on the workflow's framework and P004 reminds you.

## What each framework can do

Some features need the framework's help: reading and editing a conversation's saved state, run history and replay
from an earlier step, long-term memory, browsing an app connector's tools, and breakpoints. When a workflow's
framework cannot do one of them, AgentCanvas says so plainly instead of failing; switch the workflow to LangGraph
to use it.

## The Echo demo engine

Servers started with `AGENTCANVAS_ADAPTERS=langchain,echo` also offer **Echo**, a tiny built-in engine that shows
how a second framework plugs in. It runs simple flows (set values, paths, wait for all, approvals, questions,
Python steps), and its AI steps answer with scripted replies or repeat their prompt. It keeps paused runs in memory
and exports only the workflow file. Use it to try the framework picker, not in production.

For developers: a new framework implements the adapter core, declares what else it can do in a manifest, and must
pass the conformance kit (`apps/api/agentcanvas/conformance`); see ADR 0018.
