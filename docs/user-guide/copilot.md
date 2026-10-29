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

## Conversations, Stop and the plan

- **History** at the top of the panel lists your earlier conversations about this workflow; open one to carry on
  where it stopped. **New chat** keeps the current one there.
- **■ Stop** ends an answer while it is being written; what was said so far is kept.
- On a longer build the Copilot keeps a short **Plan** (a to-do list, "2 of 5 done") under its answer, so you see
  where it is.
- A proposal lists each change with a tick box: untick what you do not want (unticking a step unticks the wires to
  it) and **Accept** applies only what is ticked. The canvas shows the ticked changes as they would be.

## Testing

Ask "write tests for this and run them", or let the Copilot do it after building from a brief with success
criteria. It turns the criteria into test cases (input plus the expected status, field values or steps), tries the
workflow with them, and fixes what fails. Test runs use the workflow *as it would be with the proposal* and are
**dry runs**: tools that send or write to outside systems are stubbed. When no model key is set it can script the
model's replies instead of calling a model. Proposed tests are saved as the workflow's
[test cases](publish.md) when you accept.

## Debugging

The Copilot can find, explain and fix problems:

- **Problems:** it reads the Problems list with a plain explanation of every code. **✦ Ask the Copilot to fix
  these** at the top of the Problems list sends them over.
- **Failed runs:** it reads recent runs and what each step did (the same data as Explain mode), and explains the cause
  in plain words. **✦ Ask the Copilot why** under a run error asks about that run.
- **Fixes it can check:** after proposing a fix it reruns the failed run's input on the fixed workflow, replaying
  that run's recorded model replies and tool outputs when it has them, so you see whether the fix works before you
  accept it.

Ask "explain" to get only an explanation; ask "fix" to get a proposal. In Ask mode it never changes or runs anything.

## Project notes

Notes in **Settings › Copilot** are read on every message: conventions ("drafts use
ollama:llama3.3", "anything that emails customers needs an approval step"). The Copilot can propose a note too; it
is added when you accept.

## Help with code

In the [Workspace](workspace.md#copilot-help-with-code), under any Python file, the Copilot writes code from a
description, explains the file or the lines you selected, and fixes the problems ruff found. Its changes come as a
diff you accept or reject; nothing is saved until you press **Save**. It uses the same model and privacy setting as
the chat.

In the chat, the Copilot can also write the code a workflow needs (a script step's file, a tool for an agent, a
custom block, an event handler). Each file is part of its proposal, under **Writes workspace code**, with **Show
the code** and a tick box. Nothing is written until you accept; each accepted file is saved as one commit in the
workspace's history, saying it came from the Copilot and who accepted it.

## Which model it uses

Set this in **Settings › Copilot**:

- **Use the project's model** (default): the project's default model from first-run setup, or, without one, the
  first AI model in the workflow.
- **Use a separate model**: any `provider:model`, for example a stronger model for building, or a local model
  (`ollama:…`) so nothing leaves your machine. Its key is the provider's project secret, as for
  [any model](models.md).

The model must support tool calling. Your organisation's model [policies](accounts.md#policies) apply to the
Copilot too: a policy that allows only some models in workflows also limits the Copilot.

**Model for questions** (optional) answers Ask-mode questions with a different, usually cheaper, model.

To try another model in one conversation, click the model's name at the top of the panel and pick one. The list
shows only models that can answer here: hosted models whose API key is set (a project secret or a server
variable), the models your local server (Ollama, LM Studio, vLLM, llama.cpp) lists while it is running, and the
project's own OpenAI-compatible model. **Set up more models…** at the bottom opens **Settings › Models**. The same
policies and privacy setting apply; **New chat** goes back to the project's choice.

**Private** limits the Copilot to models running on your own machines (Ollama, LM Studio, vLLM, llama.cpp), so
your workflows never leave them.

**Token limit per chat** caps what one conversation can spend (input plus output tokens; input read from the
provider's prompt cache costs a tenth or less and does not count). The chat shows how much
is used; **New chat** starts over with a fresh limit.

## What it knows

The Copilot gets a knowledge pack built from the running product on every message: how workflows are put together,
every block with its ports and settings (including your workspace's own blocks), the starter templates and the edit
commands and the meaning of every problem code. Providers that support prompt caching (Anthropic) cache it, so it is
cheap after the first message: the cost shown prices cache reads and writes at their own rates. Its tools look up a block's full settings, read a template, read your workflow with
its problems, read past runs, search this user guide, and list the models and which secrets are set (names only).
An answer that used this guide ends with **From: Guide › page › section** links, which open the section in the
app's own copy of the guide. Each answer also shows the tokens it used, and its cost when the model has a price.

Text inside your workflow (prompts, descriptions) is treated as data: it cannot change what the Copilot is asked to
do. The Copilot refuses to put API keys or passwords into settings and uses secret references instead.

## For administrators

- `GET/PUT /v1/projects/{id}/copilot-settings` turns the Copilot on or off and sets its model and token limit.
- `GET /v1/workflows/{id}/copilot` returns your conversation, its brief and any open proposal;
  `POST …/copilot/messages` streams an answer (Server-Sent Events); `POST …/copilot/proposals/{id}` records
  accept or reject; `DELETE …/copilot` starts a new chat. Conversations are per person and per workflow.
- Developers can measure a model's building quality with the eval harness:
  `uv run python -m agentcanvas.copilot.evals --model anthropic:claude-sonnet-5-5` builds ten workflows from plain
  requests and checks each one validates and uses the expected blocks; `--suite debug` plants over a hundred bugs in the
  starter templates (a missing arrow to Finish, an unplugged model, a wrong output field, a broken condition, an
  unknown field in a prompt) and checks the Copilot's fix leaves no errors.
