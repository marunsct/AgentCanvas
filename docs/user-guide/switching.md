# Coming from Langflow, Flowise or n8n

Already built a flow in another visual builder? Bring it with you. On the home page, under **…or bring one from
Langflow, Flowise, n8n or another agent framework**, press **Import a file…** and pick the JSON file the other
builder exports. AgentCanvas recognises which builder wrote it and creates a new workflow:

- a file that carried over completely opens straight away;
- otherwise a note lists what changed or did not carry over (a tool AgentCanvas has no block for, a chat memory
  component, a custom code node), with a link to open the workflow. Check those points, then run it.

The same button also imports [Open Agent Spec](agents-and-connectors.md) files (from WayFlow, or pyagentspec's
LangGraph and AutoGen adapters).

## How to export from each builder

| Builder | Where the file comes from |
|---|---|
| **Langflow** | In the flow, **⋮ → Export** (or the flow's menu on the projects page → **Export**), with *Save with my API keys* switched off. |
| **Flowise** | Chatflows and Agentflows: the flow's settings (⚙) → **Export Chatflow** / **Export Agentflow**. |
| **n8n** | The workflow's **⋯ → Download**, or select all nodes on the canvas and copy them (Ctrl/⌘+C) into a `.json` file. |

API keys are never read from the file: models use the project's keys (see [Models and API keys](models.md)).

## What becomes what

AgentCanvas workflows are a sequence of **steps** that read and write named **fields**; the other builders draw
data flowing between components. The importer keeps the components that *do* something as steps and folds the rest
into them.

| In the file | In AgentCanvas |
|---|---|
| Chat Input, Chat Trigger, a chatflow's question | The workflow is a chatbot: it reads the chat (`messages`), and prompts use `{{ state.messages[-1].content }}` for the latest message |
| Text Input, form inputs, `$json.<name>` from a webhook or manual trigger | An input field `<name>` |
| Prompt / Prompt Template | The instructions of the AI step it feeds; `{variables}` become `{{ state.<field> }}` |
| A model component that answers (OpenAI, Anthropic, Gemini, Ollama, Groq…), LLM Chain, Basic LLM Chain, Flowise LLM node | **Ask AI** step with its own model block; its answer is in `<name>_reply` |
| Agent, Tool Agent, AI Agent, Flowise Agent node | **AI agent** step; the chat model plugged into it becomes its model block |
| API Request, Requests Get/Post, HTTP Request tool | **HTTP tool** block (only the URL's host is allowed) |
| If-Else / Conditional Router, If, Switch, Flowise Condition | **Path** step with a rule for each branch (equals, contains, starts with, regex, empty, number comparisons) |
| Flowise Human Input | **Ask for approval**, then a path: *proceed* or *reject* |
| Edit Fields (Set), Direct Reply | **Set values** step |
| Chat memory (Message History, Buffer Memory, Simple Memory) | Left out: a conversation keeps its messages on its own |
| Anything else (custom code, Slack, databases…) | A placeholder **Set values** step that writes a TODO, listed in the notes |

A step that needs several earlier steps (two prompts feeding one model) gets a **Wait for all** in front of it.
Branches that exclude each other (the two sides of an If) do not: whichever ran continues.

The model names in the file are kept (`openai:gpt-4.1-mini`, `ollama:llama3.3`…); a model the catalogue does not
know shows a warning in **Problems**, and you can pick another in the model block.

## Through the API

```bash
curl -X POST http://localhost:7777/api/v1/projects/prj_default/workflows/import \
  -H 'Content-Type: application/json' \
  -d "{\"file\": $(cat my-flow.json)}"
```

The answer has the new workflow, the `format` it recognised (`langflow`, `flowise`, `n8n` or `agentspec`) and the
`report` lines.
