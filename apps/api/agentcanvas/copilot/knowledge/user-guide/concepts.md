# Core concepts

A few ideas carry through the whole product.

## Organisation, project, workflow

- An **organisation** owns everything: people, projects, secrets, memory, policies. Organisations never see each
  other's data.
- A **project** groups workflows that share a **workspace** (Python code in a git repository) and **secrets**.
  Every install starts with a *Default project*.
- A **workflow** is one graph on the canvas. It has a draft you edit, **versions** you freeze, and
  **deployments** that serve a version behind an API.

## State and channels

A workflow passes one shared **state** from step to step. The state is made of named **channels** (fields), which
you declare on the Workflow panel (click the empty canvas). Each channel has:

| Setting | Meaning |
|---|---|
| **type** | A JSON Schema type (`string`, `boolean`, `array`, an object…). |
| **reducer** | How a write combines with the current value: `replace` (default), `append` (lists), `add_messages` (chat history), `sum`, or `merge` (objects). |
| **io** | Whether it is part of the workflow's input and output (`inout`), input only, output only, or `private`. |
| **storage** | `full`, or `delta` for long histories that should be stored as changes. |

Steps read channels (prompts use templates like `{{ state.ticket }}`) and write their results to channels. The
inputs and outputs of the whole workflow form its **contract**, which the API uses.

## Steps, resources and edges

- **Steps** (nodes) do work in sequence: *Ask AI*, an *AI Agent*, *Choose a path*, a script, *Ask for approval*.
- **Resources** are things steps use rather than steps themselves: a **chat model**, a **tool**, an **MCP
  server**, **middleware**, a memory backend. They sit on the canvas as dashed boxes and are wired into a step's
  typed ports (`model`, `tools`, `middleware`). Several steps can share one resource.
- **Edges** connect steps from **▶ START** to **■ END**. Routers have one output per route. Map runs a step once
  per list item in parallel; Join waits for all branches.

Ports are typed and colour-coded; a connection that doesn't fit is refused with a reason.

## Runs and threads

- A **run** executes the workflow once. It can finish, fail, or **pause** (at an approval, a human-input form, or
  a breakpoint) and be **resumed** later, even after a restart: runs are durable.
- A **thread** is a conversation: consecutive runs on the same thread keep their state, which is how chat works.
  Every step boundary is saved as a **checkpoint**, which is what the timeline, fork and replay use.

## Human in the loop

Approvals, human-input forms and tool approvals on agents (*Pause for a human before these tools run*) all pause
the run and show up in the Run panel and the cross-workflow **Inbox**. Reviewers can approve, reject, or edit the
value before approving.

## Generated code

The canvas is stored as a JSON document (the workflow IR). AgentCanvas validates it (about 70 rules, shown in the
Problems dock) and compiles it to readable LangGraph Python, visible live on the **Code** tab and exportable as a
standalone project. There is no hidden runtime: what you export is what runs.
