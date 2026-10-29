---
name: build-for-claude-agent-sdk
description: Design an AI agent as a Claude Agent SDK agent. Use when the person names the Claude Agent SDK or wants an agent in that SDK.
---

# Building for the Claude Agent SDK

The Claude Agent SDK is not a framework a whole workflow runs on here: it is the `engine` of one AI agent block, and
it talks to Anthropic models only (the model block must be an `anthropic:` model).

Before proposing the change, call `export_plan` and tell the person what it lists. Say what the SDK will not do;
promise nothing the facts below do not give.

<!-- support: filled in from the adapter manifests -->
