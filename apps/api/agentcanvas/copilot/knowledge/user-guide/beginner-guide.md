# Beginner's guide: your first AI workflow

This page assumes you have never built anything with AI before. No coding is needed. The same guide is inside the
app: open a workflow and press **❓ Help** at the top (it also opens by itself the first time you open an empty
workflow).

## What is an "agentic workflow"?

A **workflow** is a recipe a computer follows, one step at a time. Some steps use **AI** to read, write or
decide. Others ask a **person**, call a web service, or tidy up data.

An **agentic** workflow is one where an AI can also decide *what to do next* and use **tools** on its own,
for example look up an order, then write a reply based on what it found.

> Example: a customer email arrives → AI decides whether it is urgent → urgent ones go to a person for
> approval → AI drafts a reply.

## The words you will see

| Word | What it means |
|---|---|
| **Block** or **step** | A box on the canvas that does one thing, like *Ask AI* or *Ask for approval*. Drag them in from the palette on the left. |
| **▶ Start** and **■ Finish** | Every run begins at Start and ends at Finish. |
| **Arrow** (grey) | Decides what runs next. Drag from the dot on the right of a block to the dot at the top-left of the next one. |
| **AI model** | The AI “brain” (Claude, GPT, Gemini, a local model…). AI steps need one plugged into their *model* input. |
| **Add-on** (dashed outline) | Something that plugs into a step instead of running on its own: an AI model, a tool, a limit, a safety check. It connects with a coloured, dashed line. |
| **Data field** (channel) | The workflow's notepad: named boxes like `question` and `answer` that steps read and write. In text you write, insert one with `{{ state.question }}`. |
| **Agent** | An AI that works in a loop: think, use a tool, look at the result, repeat until done. |
| **Tool** | Something an agent can use, such as calling a web service or running a Python function. |
| **Run** | Trying the workflow with some input, in the **Run** tab. |

## Build your first workflow in 5 steps

Before you start, pick your AI model once (the *Set up your model* banner on Home, or **Settings › Models**). Every
new AI model block uses it automatically.

1. On the home page, type a name and press **Create empty**.
2. Click empty canvas. In the panel on the right, press **+ Channel** twice and name the fields `question` and
   `answer`.
3. Click the dot on the right of **▶ Start** and choose **Ask AI**. The block is added and already connected.
4. In the right panel, write the instructions, e.g. `Answer simply: {{ state.question }}`, and set
   **Save the reply in** to `answer`.
5. Click the coloured square marked **model** on the Ask AI block and choose **AI model**. Then click the Ask AI
   block's right-hand dot and choose **Finish (END)**.

The **Problems** list at the bottom now says *No problems — the workflow can run.* Open the **Run** tab, type
`{"question": "What is an AI agent?"}` and press **▶ Run**. The block turns green and the answer appears.

Want a person to check the answer before it is used? Click the arrow into Finish, press Delete, then click the
Ask AI block's right-hand dot and choose **Ask for approval**; connect that to Finish and run again.

Prefer to learn by doing with a checklist? On the home page, press **Start the guided build**: a card walks you
through six steps and ticks each one off as you do it.

## Try a model first: the Playground

Not sure yet what to build? Open **Playground** (top of the home page, or **Try a model in the playground**).
Pick a model, write its instructions, and chat with it. **Temperature** makes the answers more predictable (lower)
or more varied (higher); models that always use their own sampling, such as current Claude models, have it switched
off. When the answers look right, give it a name and press **Make it a workflow**: you get a workflow with one AI
agent that has those instructions, that model and that temperature, ready for tools, approvals and more steps.
Nothing you type in the playground is saved.

## Simple mode

At the top of the editor, **Simple** shows only what a first workflow needs: the blocks that ask AI, run an agent,
choose a path, set values or ask a person, with their models and tools; and the Inspector, Copilot, Run and
Versions panels, so you can still publish. **Evaluate** stays on the bar and **More** still opens the workspace,
knowledge and memory. The power tools (subgraphs, maps, frames, blast radius, tests, code) are hidden, not removed:
press **Full** to see everything again. The editor remembers your choice in this browser.

Built something in Langflow, Flowise or n8n before? [Import it](switching.md).

## Getting unstuck

- **Hover** over any dot on a block to see what it carries and what to do with it.
- **Click** a dot to see everything that can connect to it. Pick one and it is added and connected for you.
  Clicking a coloured input (like *model* or *tools*) shows the add-ons that fit there.
- **Hover** a block's title, or a block in the palette, to read what it does.
- A red **⛔** on a block means something is missing. The **Problems** list at the bottom says *what happened* and
  *how to fix it* for every problem; click one to jump to the block.
- If an arrow refuses to connect, a message at the bottom of the canvas says why and what to do instead.
- If a run fails, the Run tab explains the likely cause (a missing API key, a local model that is not running, a
  misspelt field name…) and how to fix it.
- Made a mistake? **↶** (or Ctrl/⌘+Z) undoes it. Everything saves automatically.

## Common problems and fixes

| You see | What it means | How to fix it |
|---|---|---|
| *needs a ChatModel connected to its 'model' port* | The AI step has no AI model. | Click its **model** square and choose **AI model**. |
| *no path from START to END* | A run could never finish. | Connect Start to your first step and your last step to Finish. |
| *has no outgoing flow edge* | A dead end: nothing comes after this step. | Click its right-hand dot and choose the next step, or Finish. |
| *refers to unknown state channel* | A step uses a data field that does not exist. | Click empty canvas and add the field with **+ Channel**, using the exact name. |
| *template error* | A `{{ }}` is not closed. | Every `{{` needs a matching `}}`. |
| The run says the API key is wrong | The AI provider refused your key. | Re-enter it in **Settings › Models**, or replace the secret in **Settings › Secrets**. |
| The run says connection refused | The AI model's server could not be reached. | If you use Ollama or LM Studio, make sure it is running. |

Next: browse the [Block reference](widgets.md) or read [Core concepts](concepts.md) for the details.
