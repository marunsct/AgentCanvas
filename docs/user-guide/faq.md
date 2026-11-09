# FAQ

**Do I need an API key to try AgentCanvas?**
No. Use a local model (Ollama, LM Studio…), or run from source with `make dev-fake`, which answers with built-in
demo models so every feature works offline and free. See [Run AgentCanvas on your computer](run-locally.md).

**What is the quickest way to run it?**
`make docker-up` with Docker installed, then open <http://localhost:7777> and choose your model.

**Which models are supported?**
Anthropic, OpenAI, Azure OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, any OpenAI-compatible API, and models
on your own computer through Ollama, LM Studio, vLLM or llama.cpp. All are installed with AgentCanvas. Choose one
in **Settings › Models** and enter its key there. See [Models and API keys](models.md).

**Can I use AgentCanvas without sending data to a cloud provider?**
Yes: pick a local model (Ollama, LM Studio, vLLM, llama.cpp) in **Settings › Models**. Runs, memory and traces stay
on your machine unless you turn on LangSmith tracing.

**Is the generated code mine? Can I run it without AgentCanvas?**
Yes. **Export** gives you a plain Python project (LangGraph graph, node library, your workspace code, pinned
dependencies) with a service that answers the same API as a deployment, deploy files for where you run it, and
guides; see [Export and deploy](export.md).

**What happens if the server restarts during a run?**
Runs are durable. Paused runs (approvals, human input) wait indefinitely; running runs are resumed from their
last checkpoint by another API instance.

**How do I keep an agent from doing something irreversible?**
Turn on **Blast radius** to see every step that can write, send, execute or spend. Put an **Ask for approval** step before it,
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
