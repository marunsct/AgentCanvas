# AgentCanvas user guide

AgentCanvas is a visual builder for AI agents and agentic workflows. You draw a workflow on a canvas (models,
agents, tools, routers, human approvals, memory) and AgentCanvas turns it into a real
[LangGraph](https://langchain-ai.github.io/langgraph/) / LangChain / Deep Agents Python program that you can run,
debug, test, evaluate, export and publish behind an API.

![Home page](img/home.png)

## What you can do

| | |
|---|---|
| **Use any model** | Claude, GPT, Gemini, Mistral, Groq, DeepSeek, Grok, any OpenAI-compatible API, or models on your own computer (Ollama, LM Studio, vLLM, llama.cpp). Choose one on the first-run screen, with its API key. |
| **Build** | Drag widgets onto a canvas, wire them up, and configure them in the Inspector. Start from one of seven templates, a guided six-step tutorial, or the [Beginner's guide](beginner-guide.md). Hover or click any dot on a block to see what it does and what can connect to it. Add your own Python scripts, tools and custom widgets in the project workspace. |
| **Run and debug** | Run a workflow and watch each step light up. Pause for human approvals, set breakpoints, step back through the timeline, edit state and fork, and read a plain-language explanation of what happened. See tokens, cost and latency per step. |
| **Test and evaluate** | Save runs as test cases, build datasets, score runs with evaluators (exact match, regex, schema, LLM judge, tool trajectory, your own code), compare versions in experiments, and block releases that score worse. |
| **Ship** | Freeze versions, export a standalone Python project, or publish to an environment behind an API key. Start runs from webhooks, schedules or events, and keep scoring live traffic in production. |
| **Govern** | Sign in with your identity provider, keep organisations apart, give people roles, write policies the editor and publishing enforce, and read the audit log. |

## Where to start

1. [Run AgentCanvas on your computer](run-locally.md): one Docker command, or from source with a two-minute smoke
   test.
2. [Getting started](getting-started.md): build and run your first workflow.
3. [Core concepts](concepts.md): the handful of ideas everything else builds on.

Then dive into the area you need:

- [Models and API keys](models.md)
- [Building on the canvas](canvas.md) and the [widget reference](widgets.md)
- [Running and debugging](run-and-debug.md)
- [Workspace: code, secrets and events](workspace.md)
- [Memory](memory.md)
- [Evaluation Studio](evaluation.md)
- [Publish and integrate](publish.md)
- [Organisations, roles and policies](accounts.md)
- [Deploy and operate](deploy.md)
- [FAQ](faq.md)

## Current release

This guide describes AgentCanvas **v0.5.0** (Evaluation in production). See the
[changelog](https://github.com/marunsct/AgentCanvas/blob/main/CHANGELOG.md) for what changed in each release.
