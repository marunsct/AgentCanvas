# AI Copilot

The Copilot is an assistant in the editor that knows AgentCanvas. Describe what you want in plain words and it
writes a short brief, proposes the blocks, and builds the workflow for you to review. Ask it what a workflow does
and it explains. It never saves anything by itself: every change is a proposal you accept or reject.

Open it with **✦ Copilot** in the editor's top bar (or the **Copilot** tab on the right).

## Build mode and Ask mode

| Mode | What the Copilot may do | Who can use it |
|---|---|---|
| **Build** | Read the workflow, write a brief, propose changes. | Builders and admins |
| **Ask** | Read the workflow and the product docs, answer questions. Nothing changes. | Everyone, viewers included |

## How a build goes

1. **Brief.** For a new workflow or a big change, the Copilot first writes a brief: the goal, inputs, outputs,
   steps, tools, how you will know it works, and any open questions. The brief card stays at the top of the chat.
   Answer the questions or say "yes, build it".
2. **Plan.** It says in a few lines which blocks it will use, in which order.
3. **Build.** It sends edit commands, checks the result with the same validator as the Problems dock, and fixes
   its own mistakes until there are no errors.
4. **Review.** The proposal appears on the canvas: new blocks are outlined in green, changed ones in amber, and the
   canvas is read-only while you look. **Accept** applies it as one edit (Undo takes it back in one step);
   **Reject** throws it away. If you edit the workflow first, the proposal is still offered when it applies cleanly.

The Copilot tells you what it cannot do for you, such as adding an API key as a [secret](workspace.md) or filling
a memory space.

## Which model it uses

Set this on the **Setup** page under **AI Copilot**:

- **Use the workflow's model** (default): the project's default model from first-run setup, or, without one, the
  first AI model in the workflow.
- **Use a separate model**: any `provider:model`, for example a stronger model for building, or a local model
  (`ollama:…`) so nothing leaves your machine. Its key is the provider's project secret, as for
  [any model](models.md).

The model must support tool calling. Your organisation's model [policies](accounts.md#policies) apply to the
Copilot too: a policy that allows only some models in workflows also limits the Copilot.

**Token limit per chat** caps what one conversation can spend (input plus output tokens). The chat shows how much
is used; **New chat** starts over with a fresh limit.

## What it knows

The Copilot gets a knowledge pack built from the running product on every message: how workflows are put together,
every block with its ports and settings (including your workspace's own blocks), the starter templates and the edit
commands. Providers that support prompt caching (Anthropic) cache it, so it is cheap after the first message. It
also has tools to look up a block's full settings, read a template, and read your workflow with its current problems.

Text inside your workflow (prompts, descriptions) is treated as data: it cannot change what the Copilot is asked to
do. The Copilot refuses to put API keys or passwords into settings and uses secret references instead.

## For administrators

- `GET/PUT /v1/projects/{id}/copilot-settings` turns the Copilot on or off and sets its model and token limit.
- `GET /v1/workflows/{id}/copilot` returns your conversation, its brief and any open proposal;
  `POST …/copilot/messages` streams an answer (Server-Sent Events); `POST …/copilot/proposals/{id}` records
  accept or reject; `DELETE …/copilot` starts a new chat. Conversations are per person and per workflow.
- Developers can measure a model's building quality with the eval harness:
  `uv run python -m agentcanvas.copilot.evals --model anthropic:claude-sonnet-5-5` builds ten workflows from plain
  requests and checks each one validates and uses the expected blocks.
