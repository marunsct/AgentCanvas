# Block reference

Blocks (also called widgets) are the building pieces in the palette on the left. **Steps** (solid outline) run
one after another along the grey arrows. **Add-ons** (dashed outline, such as an AI model or a tool) do not run
on their own: they plug into a step's coloured inputs.

New to all this? Start with the [Beginner's guide](beginner-guide.md). On the canvas you can hover any dot on a
block to see what it is for, and click it to see everything that can connect there.

The names below are what you see in the palette. The internal type id (for example `core.llm`) is shown in
brackets; it is what the saved workflow and the generated code use, and it never changes.

## AI

| Block | What it does |
|---|---|
| **Ask AI** (`core.llm`) | Sends your instructions (the prompt) to an AI model and saves its reply in a data field. Insert data with `{{ state.<field> }}`. Needs an **AI model** plugged into `model`. |
| **AI Agent** (`core.agent`) | An AI that can use tools on its own, step by step, until the task is done (LangChain `create_agent`). It reads and writes the conversation (the `messages` field). Plug an *AI model* into `model`, tools into `tools` and add-ons into `middleware`. Settings: instructions (system prompt), **Tools that need approval** (a person approves, edits or rejects each call) and who may approve them ([Who may decide](run-and-debug.md#who-may-decide)), and **structured output**: design a response schema with **+ Field** and the final answer is validated and written to `structured_response`. |
| **Planning Agent** (`agent.deep`) | A stronger agent for big, multi-step jobs: it writes a plan, keeps notes in files and can hand work to helper agents (`create_deep_agent`). |
| **Helper Agent** *(add-on, `agent.subagent`)* | A specialist a Planning Agent can hand a task to with its `task` tool. Fill in **When to use it**. |

**Files and images in AI steps.** When the workflow has [file or image fields](canvas.md#file-and-image-fields), **Ask AI**
and **AI Agent** show them to the model together with the message: images and PDFs as they are to a model that can see
them, and **Files to show the AI** limits a step to some of the fields (empty: all). A model that cannot see images gets a
PDF's text instead (layout, tables and scans are lost), Word documents and text files as text, and a note where an image
was left out; the editor warns about this (W025). Planning Agents do
not receive file fields yet.

## AI models

| Block | What it does |
|---|---|
| **AI model** *(add-on, `model.chat`)* | The AI “brain” as `provider:model` from any provider, hosted or local: `anthropic:claude-sonnet-5-5`, `openai:gpt-5.5`, `google_genai:gemini-3.6-flash`, `ollama:llama3.3`, `lmstudio:<model>`… A new AI model block starts as your project's default model from first-run setup. A hint under the field says where its key comes from. Optional temperature, max output tokens, timeout, Base URL, and **API key**: pick a project secret by name, or leave it empty to use the secret named after the provider. One model can power several steps. See [Models and API keys](models.md). |

## Tools

| Block | What it does |
|---|---|
| **Web API tool** *(add-on, `tool.http`)* | Lets an agent call a web service (an HTTP API). The URL is a template over the tool's inputs, e.g. `https://api.example.com/orders/{{ args.order_id }}`. Private and local addresses are refused unless the tool allows private networks; a link-local or cloud metadata address (`169.254.169.254`) is never reachable. |
| **App connector (MCP)** *(add-on, `mcp.server`)* | All tools of a [Model Context Protocol](https://modelcontextprotocol.io) server. **Browse tools** lists them and flags which ones change things, so you can pick and gate them. |
| **Python tool** *(add-on, `tool.python`)* | A LangChain `@tool` function from the workspace's `tools/` folder. |
| **Memory tools** *(add-on, `tool.memory`)* | Gives an agent `search_memory` and `save_memory` tools for one memory space. |

## People

| Block | What it does |
|---|---|
| **Ask for approval** (`core.approval`) | Pauses until a person approves or rejects, optionally editing the value shown. The decision (true/false) goes to a data field. **Who may decide** names roles, groups or people ([Run and debug](run-and-debug.md#who-may-decide)). |
| **Ask a person** (`core.human_input`) | Pauses and asks a person a question (a form when a response schema is set); the answer goes to a data field. **Who may answer** works like an approval's. |

## Flow control

| Block | What it does |
|---|---|
| **Choose a path** (`core.router`) | Sends the run down different paths: the first rule whose condition is true wins (a CEL expression such as `state.priority == "high"`), otherwise the default path. Each path is an output dot. |
| **Decide with AI** (`core.decide`) | An AI model reads the data and picks one of the paths you describe (each choice is a path name and when to choose it), for decisions a rule cannot make: what a ticket is about, whether a reply is ready to send. The decision `{choice, confidence, reason}` is saved in a data field for later steps and evaluators. The model's JSON answer is found even with text or a code fence around it, and a path name in another case (`Billing` for `billing`) counts. An answer that is not one of the choices, or a confidence below **Minimum confidence**, takes the default path. Every framework runs it the same way, and so do exports. To grade outputs the same way, use the **AI classification** evaluator. |
| **Repeat for each item** (`core.map`) | Runs the step connected to `each` once per list item, in parallel. Results must go to an `append` (list) field. |
| **Wait for all** (`core.join`) | Waits until every incoming branch has finished, then continues. |
| **Wait** (`core.wait`) | Pauses the run **for a while** (`3d`, `2h 30m`, `PT45M`), **until a time** (`2026-12-01T09:00` in a time zone you pick, UTC by default), or **until an event arrives**, with a time limit. Each can be a template, so an earlier step can choose it (`{{ state.follow_up_in }}`). The run then continues on its own; see [Waiting runs](run-and-debug.md#waiting-runs). LangGraph only. |
| **Group of steps** (`core.subgraph`) | A small workflow with its own data, run as one step. Double-click it to look inside. |

### Wait

A **Wait** step is the safe way to say "follow up in three days if nobody replied": the wait is on the canvas, checked
before the run and covered by retention and the kill switch, instead of an agent scheduling work for itself.

- **For a while**: how long, from when the run gets there: a number of seconds, amounts with units (`3d`, `2h 30m`,
  `90 minutes`, `1 week`) or an ISO 8601 duration without years or months (`PT2H30M`, `P3D`). Up to a year.
- **Until a time**: an ISO 8601 date and time (`2026-12-01T09:00`, or with `Z` or an offset) or a date (its midnight). A
  time without an offset is in the step's **Time zone** (e.g. `Europe/Berlin`; empty: UTC). A time already past does not
  wait.
- **Until an event arrives**: the event type that ends the wait (e.g. `invoice.paid`) and a **Time limit** (required).
  **Only the event with this key** (e.g. `{{ state.invoice_id }}`) makes only the event whose data has the same `"key"`
  end this run's wait; without a key, one such event ends every run waiting for it (the editor warns, W096). Events
  about runs themselves (`run.*`, `interrupt.*`) cannot end a wait.
- **Save what ended the wait in** (optional): a data field that gets `{"timed_out": false, "event": {...}, "woke_at":
  "..."}`. After an event wait, route on it with **Choose a path**, e.g. `state.reply.timed_out == true` → send a
  reminder.

The editor checks the settings it can (E097: a missing or unreadable duration, time, time zone or event, or a wait over
a year); a template is checked when the run gets there, and a value that does not work then fails the step with the
reason. Only LangGraph runs it (it needs checkpoints the server can wake days later); on another framework the editor
says so (P001) and suggests switching.

## Data and code

| Block | What it does |
|---|---|
| **Set values** (`core.set`) | Writes templated values into data fields, e.g. `summary = "Ticket: {{ state.ticket }}"`. |
| **Run Python code** (`core.script`) | Runs `def run(state) -> dict` from a file in the workspace's `scripts/` folder; return the fields to update. |
| *Workspace scripts and custom blocks* | Files in the workspace appear in the palette under **Workspace**. See [Workspace](workspace.md). |

## Safety blocks (guardrails)

Safety blocks check text as it passes through your workflow: put one right after **Start** to check what people
send in, or right before **Finish** to check what the AI wrote. Each one reads a data field (**Text to check**;
by default `messages`, the conversation, where only the newest message is checked) and has a **When it fails**
setting:

- **stop**: end the run with an error that says which block stopped it and why. Nothing after it runs.
- **flag**: carry on, and save the verdict so a later step can decide what to do. **Save yes/no in** gets
  `true` when the check fails; **Save the details in** gets the reasons (and scores). A block set to flag that
  saves nothing gets a warning, because nothing could act on it.
- **redact** (Hide personal data only, the default): hide what was found and carry on.

| Block | What it does |
|---|---|
| **Hide personal data** (`guard.pii`) | Finds email addresses, phone numbers, credit card numbers (checked with the Luhn test, so most random numbers are not mistaken for cards), IBANs, IP addresses, US social security numbers and **your own patterns** (a name and a regular expression, e.g. `order_id` = `AB-\d{6}`). **How to hide it**: *redact* (`[REDACTED_EMAIL]`), *mask* (`****-1111`, `j***@example.com`) or *hash* (a short fingerprint, the same for the same value; it is not anonymisation). It cleans the field in place (for `messages`, the message itself, so an AI step after it never sees the data) or writes the cleaned text to **Save the cleaned text in**. The details list the kinds and counts it found, never the values, and so does the error when it stops a run. |
| **Check for prompt injection** (`guard.injection`) | Scores text from 0 to 1 for attempts to take over the AI: “ignore all previous instructions”, fake system messages (`<\|im_start\|>system`, `[INST]`), requests for its hidden instructions or for keys and passwords, instructions hidden in invisible characters, text-direction tricks or base64, s p a c e d - o u t or 1337-speak spellings. It fails at the **Sensitivity threshold** (0.5). Plug in an **AI model** to also ask it; then the check fails when either says so. |
| **Check for harmful content** (`guard.moderation`) | Rates text for violence, hate, harassment, self-harm, sexual content and illegal activities, and fails when a category you chose reaches the threshold. **Plug in an AI model** for real judgement (it answers with a score per category; a reply that leaves a category out is not trusted, and the fixed phrases decide). The model cannot clear a near-certain phrase match (such as instructions to make a drug). Without one, a fixed list of phrases is used: it catches blatant cases and ignores everyday phrases like “kill the process”, but it is no substitute for a model. |
| **Check the output format** (`guard.schema`) | Checks that a field (or the newest message) is JSON matching the format you design with **+ Field**, e.g. an object with a `summary` text and a `priority` of low, medium or high. It finds the JSON in a reply even inside a ```` ```json ```` block or a sentence. With an AI model plugged in and **Let the AI fix it once** on, an invalid reply gets one correction before the check fails. **Save the checked data in** receives the parsed data. |

**Branch on a verdict.** Set **When it fails** to *flag* and **Save yes/no in** to a true/false field, say
`flagged`. Add a **Choose a path** step after the safety block with a rule `state.flagged == true` → path
`blocked`, and the default path `ok`. Connect `blocked` to a step that answers politely (for example **Set
values** `answer = "Sorry, I can't help with that."`) and `ok` to the normal next step. With **Save the details
in** set to `check`, rules can also use `state.check.score > 0.8` or `"violence" in state.check.flagged_categories`.

**Measure a safety block on your own data.** Each block ships with labelled test cases (12 to 28 each, including
tricky harmless ones like “how do I kill a stuck process?”), in `apps/api/agentcanvas/registry/guardrails/`. Run
`python -m agentcanvas.guardrails guard.injection > cases.jsonl` (in `apps/api`) and import the file into a
dataset of a workflow whose input is `text` and output `flagged` (Evaluation page → **Import CSV or JSONL**), then run
an experiment with an `exact` evaluator limited to the `flagged` channel. Add your own examples to the dataset to see how the block
does on your traffic before you rely on it.

Fixed checks are fast and free but can be fooled by new wording; treat them as a first line, and combine them with
an AI model and, for anything risky, a person (**Ask for approval**).

## Memory

| Block | What it does |
|---|---|
| **Look up memory** (`core.memory_recall`) | Searches long-term memory and writes the matches to a data field. |
| **Save to memory** (`core.memory_remember`) | Saves a field's value in long-term memory under a key. |
| **Forget memory** (`core.memory_forget`) | Deletes one memory by key. |
| **Saved files folder** *(add-on, `backend.store`)* | Mounts a Planning Agent folder (e.g. `/memories/`) on long-term memory. |
| **Scratch files** *(add-on, `backend.state`)* | Planning Agent files kept with the conversation: they live and die with it. |

See [Memory](memory.md) for spaces and sharing.

## Agent add-ons

Add-ons (middleware) change how an agent's model and tool calls behave. Plug one or more into an agent's
`middleware` input; the order of the lines is the order they apply: the first one connected wraps the ones after
it. So connect **Retry AI calls** before **Backup models** (each attempt can then fall back), or set the retry's
"When retries are exhausted" to error; otherwise the retry answers the failure itself and the backup models are
never tried (warning W017).

| Block | What it does |
|---|---|
| **Protect personal data** (`mw.pii`) | Finds personal data in messages and blocks, redacts, masks or hashes it. |
| **Limit AI calls** / **Limit tool use** (`mw.model_call_limit`, `mw.tool_call_limit`) | Stop the agent after N model or tool calls per run or conversation. |
| **Retry AI calls** / **Retry tools** (`mw.model_retry`, `mw.tool_retry`) | Retry failed calls, waiting a little longer each time. |
| **Backup models** (`mw.model_fallback`) | Try the connected models in order when the main model fails. |
| **Shorten long chats** (`mw.summarization`) | Summarise old messages when the conversation grows too long. |
| **To-do list** (`mw.todo`) | Give the agent a `write_todos` tool to plan multi-step work. |
| **Pick tools first** (`mw.tool_selector`) | Let a cheaper model pick the relevant tools before each call. |
| **Quality check** (`mw.rubric`) | Before answering, a grader model checks the work against your checklist and sends it back with what is missing, up to a set number of rounds. Each round shows live in the Run panel. |
