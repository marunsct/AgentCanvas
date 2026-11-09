# 19 — Threat model

What AgentCanvas protects, from whom, and where each protection lives in the code. Written after the deep review of
2026-10-29 (HARDENING S-1 to S-11); keep it current when a boundary moves. A change that weakens one of the
boundaries below needs a decision in [decisions.md](../decisions.md) and a test that shows the new boundary.

## What is worth protecting

| Asset | Where it lives |
|---|---|
| Provider and tool keys (project secrets) | `secret` table, Fernet-encrypted with a per-organisation data key wrapped by the KMS master key (`security/kms.py`); decrypted into `SecretCache` in each API process |
| Conversations, runs, recordings, knowledge, memory | Postgres, every tenant table under row-level security (`migrations/…rls…`), the API connecting as a role without `BYPASSRLS` |
| Workspace code | a git repository per project under `workspaces_dir` |
| Deployment keys, share tokens, personal tokens | stored as SHA-256 hashes, compared with `hmac.compare_digest` |
| The server itself | database credentials, the KMS key, `AGENTCANVAS_SECRET_KEY`, the sandbox and egress tokens (environment of the API process only) |

## Who is in the picture

| Actor | Trusted with |
|---|---|
| **Admin** of an organisation | everything in it: members, policies, secrets, server-wide settings they are shown |
| **Builder** | the project's workflows, workspace code and the *use* of its secrets (a builder can make a workflow send a secret to a host they choose, on purpose, by naming it; that is the trust a builder has). Not: reading secret values back, reaching the server's own internals, or other organisations |
| **Viewer / reviewer** | reading and reviewing, nothing that runs code or changes workflows (`tests/test_roles_every_route.py`) |
| **Deployment caller** (a key) | running that one deployment and its conversations; nothing else (`api/v1/deployments.py` `own_thread`) |
| **Share-page visitor** | talking to that one deployment under its rate limits and budget |
| **Workspace code** (scripts, tools, custom blocks, event handlers) | its own call's input and the hosts its project's `sandbox.json` lists; **untrusted**, even when a builder wrote it, because a builder's code runs where other tenants' data is |
| **Model and tool outputs** | data only: they can steer a model, never the server |

## Boundaries and what holds them

### 1. Workspace code ↔ the server and other tenants
- Staging and production refuse to run workspace code in the API's own process or as its user (`inprocess`,
  `subprocess`) unless the operator says so (`check_auth_settings`, S-8).
- **The sandbox service** (`script_execution=remote`) runs code in a container with no database credentials, no
  keys, a read-only root, no capabilities and two internal networks only: `sandbox` (whose way out is the egress
  proxy) and `sandbox-control` (where the API calls it). The API answers 403 to every request from the control
  network (`api/limits.py` `RefuseClients`, S-3).
- One call at a time per sandbox container; after each, every process the call started (daemons included, through a
  child subreaper) is killed and what it wrote to `/tmp` removed (`sandbox/service.py` `sweep`, S-2). The child is
  not dumpable and receives its egress credential on stdin, so no other process of the same user reads it (S-2).
- A cancelled run stops its code: the child or container is killed, and the service stops a call whose caller went
  away (R-2).
- The in-process network block in `sandbox/child.py` is a guard against mistakes, not a wall: code that imports
  `_socket` goes around it. The walls are the container networks and the egress proxy.

### 2. Workspace code ↔ the network
- No network unless `sandbox.json` lists hosts; then only through the egress proxy, which checks a list the API signed
  for that call (`sandbox/egress.py`). The credential lives for the call's time limit plus the wait for a sandbox
  and names the call in the proxy's log (S-7).
- The proxy never reaches link-local or cloud metadata addresses, and reaches private addresses only when its operator
  sets `AGENTCANVAS_EGRESS_ALLOW_PRIVATE=1`; naming an address in `sandbox.json` opens nothing (S-1). Plain-HTTP
  requests go out with the checked `Host` and no hop-by-hop headers; a request framed two ways is refused (S-7).
- DNS is a way out too: a name the code looks up reaches whatever resolves it. In the Helm chart the sandbox pods'
  NetworkPolicy admits the egress proxy's port only, with no DNS; the API hands the sandbox the proxy's ClusterIP
  (`AGENTCANVAS_EGRESS_PROXY_RESOLVE`), and target names are resolved by the proxy. With compose the sandbox reaches
  Docker's embedded resolver on its internal network (it gives the proxy's address on that network, which the API
  cannot know); whether that resolver forwards other names out depends on the Docker version, so treat it as a
  narrow channel there.

### 3. Workflows ↔ the network (tools, MCP, models, knowledge pages)
- HTTP tools reach only their `allowed_domains`, connect to the address they checked (no DNS rebinding), and refuse
  private addresses unless the block allows them; an organisation policy can forbid that switch (accounts.md).
- In the hosted edition MCP servers and OpenAI-compatible model clients connect only to public addresses checked at
  connection time (`adapters/langchain/pinned.py`, S-5).
- A provider key stored for a provider or a saved server goes only there: a model pointed at another address gets a
  key only when its own settings name one (S-4).

### 4. Secrets ↔ outputs
- Values are never returned by the API; they are masked as `***` in run events, traces, error messages and
  exports, in their raw, base64, URL-encoded and JSON-escaped forms; a secret needs at least 6 characters so it can
  be masked (S-9). Errors raised before an adapter could mask them are masked by the runner (R-7).
- A key typed into a header instead of a secret reference is refused by every export and flagged on the canvas (X-1,
  W021). A rotated key builds new clients at once (R-3).
- Accepted risk: every organisation's decrypted values sit in one cache per API process. Code that runs inside the
  API could read them, but such code could also read the KMS key; the defence is that no tenant code runs there
  (boundary 1). Recorded in [decisions.md](../decisions.md).

### 5. Tenants ↔ each other
- Row-level security on every tenant table, the API as a non-`BYPASSRLS` role; the cross-organisation sweep over the
  id routes (`tests/test_org_*`), and the every-route auth and role sweeps.
- Deployment, MCP and A2A callers see only their deployment's conversations: another deployment's thread is the same
  404 as an unknown one (S-6). nginx rate-limits every public endpoint per visitor address, trusting
  `X-Forwarded-For` only from its own network (S-3, S-6).

### 6. The Copilot ↔ the project
- Text in workflows, runs, workspace files and the guide is data to the Copilot; it can propose changes and code, never
  apply them: a builder accepts, after seeing every change and the whole code of every file (`decide_proposal`, a
  browser test, S-11). It refuses to put keys into settings.

## Out of scope (stated, so nobody assumes otherwise)
- A malicious **admin** of an organisation, or whoever runs the server.
- Model providers' own handling of prompts (choose a local model and the Copilot's privacy setting to keep data in).
- Side channels between sandbox containers on one host (CPU timing): run the sandbox on separate nodes for tenants
  that require it (Helm `sandbox.nodeSelector`).

## How to check it still holds
`make ci-full` runs: the auth, role and cross-organisation sweeps; the sandbox, egress and pinned-client tests; the
`images` stage (Trivy, probes through nginx including the share-page framing); the hostile-input and fuzz tests.
