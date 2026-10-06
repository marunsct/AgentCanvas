# FAQ

**Do I need an API key to try AgentCanvas?**
No. `make dev-fake` uses built-in demo models, so every feature works offline and free; answers are canned. Use
`make dev-api` with a provider key for real answers. See [Run AgentCanvas on your computer](run-locally.md).

**Which models are supported?**
Any chat model LangChain's `init_chat_model` can create, written as `provider:model`. The catalog lists Claude
Opus 5.5, Claude Sonnet 5.5, Claude Haiku 4.5, GPT-5.5 and GPT-5 mini. Anthropic support is installed; for other
providers install their LangChain package (e.g. `langchain-openai`) on the server.

**Is the generated code mine? Can I run it without AgentCanvas?**
Yes. **Export** gives you a plain Python project (LangGraph graph, node library, your workspace code, pinned
dependencies, Dockerfile, `langgraph.json`) that runs and serves on its own.

**What happens if the server restarts during a run?**
Runs are durable. Paused runs (approvals, human input) wait indefinitely; running runs are resumed from their
last checkpoint by another API instance.

**How do I keep an agent from doing something irreversible?**
Turn on **Blast radius** to see every step that can write, send, execute or spend. Put an **Approval** before it,
or list the tool under the agent's *Pause for a human before these tools run*. Use **Dry run** to test safely,
and an organisation policy such as *Writes need approval* to make it mandatory.

**Where are API keys and passwords stored?**
As project **secrets**: encrypted at rest (under a master key you can keep in AWS or Google Cloud KMS), never
readable through the API, and masked as `***` in run events and traces.

**Can several people work on the same workflow?**
Yes. Edits save automatically with conflict detection: if someone else saved first, you are told instead of
overwriting their work. Roles decide who can build, review or only view.

**How do I call a workflow from my application?**
Publish it, copy the API key, and `POST` to `/v1/deployments/{id}/runs/wait` or `/runs/stream`. See
[Call a deployment](publish.md#call-a-deployment).

**How do I know a new version is better?**
Build a dataset, add evaluators, run an experiment comparing the live version with the draft, and set a release
gate so publishing is refused when scores drop. See [Evaluation Studio](evaluation.md).

**How do I delete a user's data?**
Erase their memory namespace on the **Memory** page. Conversations are deleted automatically after the retention
period (90 days by default).

**Where do I report a bug or see what's planned?**
On [GitHub](https://github.com/marunsct/AgentCanvas). `PROGRESS.md` lists what is done and pending.
