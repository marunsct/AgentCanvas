---
name: human-approval
description: Add a person to the loop - approve a step's output, approve an agent's tool call, or ask for an answer - and explain how decisions reach the Inbox. Use for requests like "a manager must approve", "ask before sending", "let a person check".
---

# A person in the loop

Three ways, pick the one that matches the request:

- **Approve before a tool runs**: on an AI agent (`core.agent`), list the tool's name in `approve_tools`. The run
  pauses before that tool; the person approves, edits the arguments, or rejects with a reason the agent reads.
  Use this for tools that send, pay, delete or write somewhere (the validator warns with W002 when they are not
  approved).
- **Approve a value**: a `core.approval` step after the step that drafts it. `show` is the data field the person
  sees (and may edit); `output` gets true or false. Route on it with a router (`state.approved == true`).
- **Ask for an answer**: `core.human_input` with a `prompt` and optionally a `response_schema`; the answer is written
  to `output`.

Pending decisions appear in the Inbox (top bar) for anyone with the reviewer role, and in the editor's Run panel.
A step must not both pause for a person and change something outside (E070): split it into two steps.
Test it with `run_test`: a pause shows as an interrupted run.
