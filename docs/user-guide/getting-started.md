# Getting started

This walk-through builds and runs a small workflow in about ten minutes. It assumes AgentCanvas is running
([Run AgentCanvas on your computer](run-locally.md)). With the demo models (`make dev-fake`) every step works
without API keys; answers are canned.

## Choose your model

On first start, Home shows **Set up your model**. Click **Choose a model**, pick a provider (hosted, or a local
server such as Ollama), enter the key if it needs one, **Test connection** and **Save**. Templates then use that
model. See [Models and API keys](models.md). You can skip this when running with the demo models.

## Option A: the guided build

On **Home**, click **Start the guided build**. A six-step tutorial walks you through state, steps, flow, a model, a
run and a human approval, and advances by itself as the real canvas takes shape. It is the fastest way to learn
the editor.

## Option B: start from a template

On **Home**, the [template gallery](templates.md) creates a ready-to-run workflow from one of 38 templates, beginner to advanced:
customer support bots, content writers, data extraction, research, sales, HR, engineering and operations helpers.
Search it or pick a category, then click a card. The ones to start with:

| Template | What it shows |
|---|---|
| **Chat assistant** | One agent chatting over the `messages` channel. The best starting point for adding tools. |
| **Classify and route** | An LLM classifies the input, a router picks a branch, each branch writes a reply. |
| **Approval workflow** | An LLM drafts, a person approves or edits, and only approved drafts are published. |

**Support triage (demo tools)** is a variant of Support triage wired to a demo `send_reply` tool from the
examples, used in the smoke test.

## Your first run

1. Click **Classify and route**. The editor opens:
    - the **palette** of widgets on the left,
    - the **canvas** in the middle,
    - the **Inspector / Copilot / Run / Tests / Versions / Code** tabs on the right,
    - the **Problems / Timeline / Explain / History** dock at the bottom (History lists the workflow's runs).
2. Click a step on the canvas. The Inspector shows its settings: prompt, output channel, model.
3. Open the **Run** tab. The input box is pre-filled with the workflow's inputs as JSON. Fill in a value and
   click **▶ Run**.
4. Steps light up as they run and show a *done* badge with tokens and time. The Run tab streams output, then shows
   the final state.
5. Open the **Explain** tab in the bottom dock for a step-by-step narrative ("chose route 'normal' → …").
6. Open the **Code** tab to see the LangGraph Python program AgentCanvas generated from your canvas. It updates as
   you edit.

## Make a change

1. Drag an **Ask for approval** block from the palette (*People*) onto the canvas.
2. Drag from the output handle of one step to the input of the Approval, and from it onward. Connections
   are checked as you draw; if one is refused, a message says why. Tip: click a block's right-hand dot to see
   what can come next and add it already connected.
3. In the Inspector, choose which data field the reviewer sees (*What the person reviews*) and where the
   decision is written (*Save the decision in*).
4. Watch the **Problems** dock: it lists anything that would stop the workflow from running, and says
   *No problems — the workflow can run.* when it is ready.
5. Run again. The run pauses with **Approval needed**; approve or reject in the Run tab, or from the **Inbox**.

Changes save automatically (the header shows *Saved*). Undo and redo with the ↶ ↷ buttons or ⌘Z / ⇧⌘Z.

## Next steps

- New to AI? Read the [Beginner's guide](beginner-guide.md).
- Learn the vocabulary in [Core concepts](concepts.md).
- Add a tool to the Chat assistant: see [Widgets › Tools](widgets.md#tools).
- Save the run as a test case and freeze a version: see [Publish and integrate](publish.md).
- Let the [Copilot](copilot.md) build or fix a workflow from a plain description, try a model in the
  [Playground](settings.md#the-top-bar), and follow every run on the **Runs** page
  ([Settings and moving around](settings.md#your-workflows-and-the-runs-page)).
