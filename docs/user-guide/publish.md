# Publish and integrate

Everything about shipping lives on the editor's **Tests** and **Versions** tabs.

## Tests before you ship

- After a run, **Save as test case** (Run tab) stores the input and the expected final status, route and output.
- The **Tests** tab lists the cases and runs them all with **▶ Run tests**, against the draft or a version.
- **Record as test** turns a recorded run, including a production run, into a regression test that replays the
  recorded model and tool answers, so it is deterministic and needs no network.

## Versions

**Save version** freezes the workflow together with its workspace commit as a semantic version (`1.0.0`, …).
Pick two versions to see a field-level diff drawn on the canvas, and **Restore** an old one into the draft.

The **Contract** panel shows the workflow's API: input and output fields and where it may pause. Publishing
reports breaking contract changes (a removed input, a changed type) against the version currently live.

## Export

**⬇ Export to run in your own environment…** downloads a project you deploy yourself (your computer, a Linux
server, Kubernetes, Cloud Run, AWS or Azure), with a service that answers the same API as a deployment and guides
written for the workflow. See [Export and deploy](export.md).

## Pull requests for versions

A workflow connected to a Git branch opens or updates a pull request with its export and the diff of what changed on
every publish. See [Git round trip](export.md#git-round-trip-a-pull-request-for-every-change).

## Publish

**Publish** (or **Publish to prod**) creates a **deployment**: one per environment (`prod`, `staging`, `dev`, …),
always pinned to a version. Editing the draft never changes what callers get until you publish again, and neither
does an app connector's server: the version offers only the tools you browsed, as they were then
([Published tools are pinned](agents-and-connectors.md#published-tools-are-pinned)).

Publishing first runs a checklist, and refuses on a failure:

| Check | Passes when |
|---|---|
| validation | the workflow has no errors; every app connector's tools were browsed, so they can be [pinned](agents-and-connectors.md#published-tools-are-pinned) |
| policies | every organisation policy holds |
| secrets | every `{"$secret": …}` reference exists |
| tests | the saved test cases pass |
| contract | no breaking change against the live version (or it is the first publish) |
| release gate | the evaluation meets the environment's gate, if one is set |
| system record | always (a reminder, never a refusal): lists the notes of the environment's [AI-system record](#ai-system-record), once someone has filled it in |

The deployment's first **API key** is shown **once**, when the deployment is created. Store it safely.
**Unpublish** removes the deployment and revokes its keys; publishing it again makes a new key.

### Deployment keys

A deployment can have several keys at once (up to 20 that work), each with a name and, if you like, an expiry, so
each caller can have its own and a key can be replaced without downtime. Only a fingerprint (SHA-256) of a key is
stored, and a key is shown once, when it is made. Builders manage them under the deployment in the Publish panel:
**API keys** lists the working keys (name, first characters, expiry, last use; **Show revoked and expired keys** adds
the others), **Add key** makes one with a name and an expiry (never, 30 days, 90 days or a year) and shows it once,
and **Revoke** stops one. **Rotate key** asks how many hours the current keys keep working (0 stops them at once) and
shows the new key. The same through the API:

| Request | Does |
|---|---|
| `GET /v1/deployments/{id}/keys` | The keys, newest first: name, first characters, who made it, when it expires, when it was last used (to the minute), and whether it was revoked. Never the key itself |
| `POST /v1/deployments/{id}/keys` with `{"name": "CI", "expires_in_days": 90}` | Another key next to the ones that work (`expires_in_days` left out: it never expires). The answer holds `api_key`, shown this once |
| `DELETE /v1/deployments/{id}/keys/{key id}` | Revoke the key: calls with it are refused at once, on every server |
| `POST /v1/deployments/{id}/rotate-key`, optionally with `{"overlap_hours": 24}` | A new key; the other keys stop working at once, or after the overlap (at most 720 hours) |

To replace a key without downtime: make a new one, move the callers to it, check that the old one's last use stops
moving, then revoke it (or rotate with an overlap, which does the same on a timer). A revoked or expired key gets
`401` with a message that says which. Making, revoking and rotating keys are in the audit log
(`create_deployment_key`, `revoke_deployment_key`, `rotate_key`) with the key's first characters, never the key.

### AI-system record

Each deployment keeps an **AI-system record**: the page an auditor, a risk owner or your AI inventory asks for
(ISO/IEC 42001, NIST AI 600-1, the EU AI Act's deployer duties; see
[AI-system inventory](security.md#ai-system-inventory)). People write a few fields, all optional:

| Field | What to write |
|---|---|
| Purpose | What the system is for, and what it must not be used for |
| Owner | The member who answers for it |
| Risk class | `minimal`, `limited` or `high`, with a rationale (required with a class): *high* is for decisions about people's access to work, credit, education, essential services or safety |
| Data categories | The kinds of data it handles, e.g. personal data, health data, financial data |
| End-user facing | Whether its end users are the public (customers, visitors) or only your own staff; for staff only, the share link's AI notice defaults to wording for colleagues (it is still shown) |
| Review date | When the record is next reviewed |

In the Publish panel, open **System record** under a deployment: builders fill in the fields and press **Save
record**; everyone sees its notes and a line summing up the evidence, and can **Download** it as Markdown or as an
HTML page (open it in a browser and print it to get a PDF).

Everything else is read from what the server keeps, at the moment you open the record, so it never falls out of
date: the published version and what changed since the version before it; the models and their providers, and every
other service its runs send data to (app connectors, web APIs, remote agents, judge models, the trace export); its
tools, with the fingerprints of the app connector tools pinned at publish; its guardrails (safety steps, agent
add-ons, the budget) and the organisation's policies for its environment; where a person approves, and which steps
act outside without anyone approving; evaluation evidence (the release gate, the latest experiment and red-team run,
online evaluators' scores over 30 days); who published it, unpublished it and changed its record; and its retention
and data handling.

The record also lists **notes**: reminders, never errors, of what an auditor would ask about. A deployment marked
high risk with no evaluation evidence, with no step that asks a person, or without an owner gets one; so does an
owner who left the organisation and a review date that passed. Publishing lists them in its checklist
(**system record**) and goes ahead.

| Request | Does |
|---|---|
| `GET /v1/deployments/{id}/system-record` | The whole record as JSON (viewers and above) |
| `GET /v1/deployments/{id}/system-record/export?format=markdown` (or `html`) | The record as a document to hand an auditor or file in your AI inventory: Markdown, or an HTML page that prints as a PDF. Each download is in the audit log (`export_system_record`) |
| `PUT /v1/deployments/{id}/system-record` with `{"purpose": "…", "owner_id": "usr_…", "risk_class": "high", "risk_rationale": "…", "data_categories": ["personal data"], "end_user_facing": true, "review_date": "2027-05-01"}` | Replace the fields people write (builders and above); a field left out is cleared. Refused when the owner is not a member or a risk class has no rationale |

Changing the record is in the audit log (`update_system_record`, with the risk class, owner and review date) and is
forwarded as a security event.

### Sign-in with OAuth

A deployment can also let people sign in with your identity provider instead of sharing a key: its
[MCP server and A2A agent](agents-and-connectors.md#call-a-workflow-from-other-agents), its
[AG-UI endpoint](#connect-a-copilotkit-style-front-end-ag-ui) and its [deployment API](#call-a-deployment)
(`runs/wait` and the others). It is **off** until a builder turns it on for a deployment; the deployment's keys keep
working beside it. With it on:

- **Clients find where to sign in on their own.** A call without a token, or with one that is not accepted, answers
  `401` with `WWW-Authenticate: Bearer resource_metadata="…", scope="…"`. That address is the endpoint's protected
  resource metadata (RFC 9728), which names the **authorization server**: the issuer you set for the deployment, or
  else the one people sign in to AgentCanvas with (`AGENTCANVAS_OIDC_ISSUER`). Clients that follow the MCP
  authorization spec (Claude, ChatGPT, VS Code and others) then sign the person in and call with
  `Authorization: Bearer <access token>`.
- **Only tokens for this endpoint work.** The token must be a JWT signed with a key the issuer publishes (its
  discovery document's `jwks_uri`), from that issuer, not expired, and for this endpoint: its audience (`aud`) is the
  endpoint's address, as clients ask for it with RFC 8707's `resource`, or the **audience** you set for the
  deployment. A token issued for another server is refused, and so is one endpoint's token on another. The addresses:

  | Endpoint | Its address (the token's audience) |
  |---|---|
  | MCP server | `https://<your AgentCanvas>/api/v1/deployments/<id>/mcp` |
  | A2A agent | `…/v1/deployments/<id>/a2a` |
  | AG-UI | `…/v1/deployments/<id>/ag-ui` |
  | Deployment API (`runs/wait`, `runs`, `runs/stream`, `runs/{run_id}`, `cancel`, `feedback`, `threads/…/resume`, `files`, `end-users/…`) | `…/v1/deployments/<id>`: one address for all of them |

- **Scopes.** A token without every **scope** the deployment requires (by default `agentcanvas:deployment:run`;
  `scope` or `scp` claims both count) gets `403` with `error="insufficient_scope"`. Reading through the deployment
  API (`GET …/runs/{run_id}` and `end-users/export`) also takes `agentcanvas:deployment:read` alone, so a client that
  only shows results can be given just that.
- **Every person is their own end user.** The token's subject becomes the run's `user_id`, written
  `oidc:<issuer>:<subject>` (a `user_id` in the request, or AG-UI's `forwardedProps.user_id`, is ignored): their
  memories are their own, and they can continue, read, stop and rate only the conversations, runs and tasks they
  started (another person's answers `404`, like an unknown one; an AG-UI `threadId` of another person's is "taken",
  `409`). The files they upload are theirs too: another person's run cannot use them. `end-users/export` and
  `end-users/erase` with no `user_id` give or erase their own data, and they may name no one else (`403`). A
  deployment key acts for your own systems as before: it can continue, read and erase any of the deployment's
  conversations and end users, so give keys only to your own back end.
- **Callbacks need a key.** A run started with a token cannot ask for a `callback_url` (`422`): callbacks are signed
  with the deployment's secret, which your own systems trust. Read the result with `GET …/runs/{run_id}`.

Builders turn it on under the deployment in the Publish panel: **Sign-in with OAuth** takes the issuer
(empty: the one people sign in to AgentCanvas with), an optional audience and the required scopes, and **Turn on**
checks the issuer and shows the addresses to register with your identity provider; **Turn off** goes back to keys
only. The **Use from other agents** panel then says that clients which support sign-in need only the address. The
same through the API:

| Request | Does |
|---|---|
| `GET /v1/deployments/{id}/oauth` | Whether it is on, the issuer tokens must come from, the audience and scopes, and the addresses of the endpoints and the deployment API with their metadata addresses (what your identity provider needs to know) |
| `PUT /v1/deployments/{id}/oauth` with `{"enabled": true}` | Turn it on with the server's sign-in issuer and the default scope. Optional: `"issuer": "https://login.example.com/…"` (another authorization server), `"audience": "api://agentcanvas"` (for identity providers that issue tokens for a fixed audience rather than an address, such as Microsoft Entra ID or an Okta authorization server), `"scopes": ["…"]` (every one required; `[]` for none) |
| `PUT /v1/deployments/{id}/oauth` with `{"enabled": false}` | Turn it off: only the keys work again |

Turning it on loads the issuer's signing keys at once, so a wrong address is told then (`502`); the issuer must be an
`https://` address, and on the hosted edition a public one. In staging and production `AGENTCANVAS_PUBLIC_URL` must be
set: tokens are bound to the endpoint's public address, which must not come from what a caller sends. Changes are in
the audit log (`put_deployment_oauth`, forwarded as a security event), and each signed-in call's entry names its end
user. **Unpublish** turns sign-in off with the keys; publishing again does not turn it back on.

Each server keeps an issuer's keys for `AGENTCANVAS_OIDC_JWKS_CACHE_S` (one hour), and loads them again at once when a
token is signed with a key it has not seen (the issuer rotated its keys). Register the endpoint with your identity
provider as an API (resource) whose identifier is the endpoint's address or your audience, with the scope the
deployment requires; the web server serves the metadata at `/.well-known/oauth-protected-resource/api/v1/deployments/…`
(a proxy in front of it must pass that path on). The deployment API's metadata is the deployment's own
(`…/oauth-protected-resource/api/v1/deployments/<id>`), and lists `agentcanvas:deployment:read` beside the required
scopes.

**Enterprise-Managed Authorization.** With MCP's enterprise-managed authorization extension (Identity Assertion
Authorization Grant, ID-JAG), the organisation's identity provider decides which agents its people may connect,
and the client trades the person's sign-in for an access token without a consent screen. Nothing is needed on the
AgentCanvas side: the token comes from your identity provider and is checked like any other, so name that identity
provider as the deployment's issuer.

## Call a deployment

```bash
curl -X POST http://localhost:8000/v1/deployments/<deployment id>/runs/wait \
  -H "Authorization: Bearer <api key>" \
  -H "Content-Type: application/json" \
  -d '{"input": {"messages": [{"role": "user", "content": "Hello"}]}}'
```

The answer:

```json
{"run_id": "run_…", "thread_id": "th_…", "status": "success",
 "output": {"messages": [ … ]}, "interrupts": [], "error": null,
 "generated_by": {"kind": "ai", "models": ["anthropic:claude-sonnet-4-6"]}}
```

**AI-generated output is marked.** Every run answer of a deployment (`runs/wait`, `runs/stream`, `resume`, MCP
`tools/call`, A2A `message/send` and `SendMessage`, AG-UI, and the share-link chat) has the response header
`X-AI-Generated: true`. API results and the stream's `RUN_FINISHED` event also carry `generated_by`: `kind` is always
`"ai"`, and `models` lists the models that answered in the run, in the order they first answered (empty when the run
called no model). Keep the mark with the content when you pass it on, so your own systems and your users can tell it
was generated by AI ([EU AI Act](security.md#eu-ai-act)).

| Endpoint | Use |
|---|---|
| `POST /v1/deployments/{id}/runs/wait` | Run and wait for the result. |
| `POST /v1/deployments/{id}/runs` | Start a run and answer at once (`202`, `"status": "running"`); read the result later. |
| `GET /v1/deployments/{id}/runs/{run_id}` | A run's status, and once it ended its `output`, `interrupts` or `error`. |
| `POST /v1/deployments/{id}/runs/{run_id}/cancel` | Stop a run: `{"cancelled": true, "pending": false}`, or `"pending": true` when it runs on another server, which stops it within a few seconds. |
| `POST /v1/deployments/{id}/runs/stream` | Run and receive Server-Sent Events as it goes. |
| `POST /v1/deployments/{id}/threads/{thread_id}/resume` | Answer a pause: body `{"value": true}`, plus `interrupt_id` naming the pause. You may leave the id out while only one pause waits; with several waiting the call is refused with `409` and the waiting ids in `pending` ([Several pauses at once](run-and-debug.md#several-pauses-at-once)). A step that names who decides it answers `403` unless it lets the caller answer too ([Who may decide](run-and-debug.md#who-may-decide)); it waits in the Inbox instead. The MCP `answer` tool and an A2A reply are refused the same way, and the paused run's MCP result or A2A task says that a person decides it in AgentCanvas instead of asking the caller to answer. |
| `POST /v1/deployments/{id}/ag-ui` | Run for an AG-UI front end (CopilotKit and others): an AG-UI `RunAgentInput` in, AG-UI events out. See [Connect a CopilotKit-style front end](#connect-a-copilotkit-style-front-end-ag-ui). |
| `POST /v1/deployments/{id}/runs/{run_id}/feedback` | Send `{"rating": "up" \| "down", "comment": "…"}`. |
| `POST /v1/deployments/{id}/end-users/export` | One of your end users' data as JSON: body `{"user_id": "…"}`. Their conversations on this deployment (each run's input, answer and steps), long-term memories and the names and sizes of the files their runs used. A person [signed in with OAuth](#sign-in-with-oauth) sends `{}` and gets their own. |
| `POST /v1/deployments/{id}/end-users/erase` | Erase one of your end users: body `{"user_id": "…"}`, the id you sent with their runs. Their conversations on this deployment, long-term memories and the files their runs used are deleted; the answer gives the counts. A person signed in with OAuth sends `{}` and erases their own. |

Request fields: `input` (the workflow's input channels), `thread_id` (continue a conversation), `context`, and
`user_id` (your end user: their memories are kept apart, and a conversation remembers whose it is, so you can
erase it on their request). When `status` is `interrupted`, `interrupts` says what the
run is waiting for. When the organisation has a runs-at-once limit and it is reached, a run waits for a free slot
first (see [Settings](settings.md)), and the wait counts towards `runs/wait`'s 5 minutes (after them it answers
`"status": "running"`); a run that waits too long for its slot ends with an error whose code is `queue_timeout`.

### Long runs: start now, read the result later

A run that may take longer than 5 minutes, or wait in the run queue, is better started with `POST …/runs`, which
answers at once with the `run_id` (and its address in the `Location` header). Ask
`GET /v1/deployments/{id}/runs/{run_id}` every few seconds: it says `running` while the run works or waits for a
slot, then gives the same answer `runs/wait` would have. Any API server answers, also after the one that ran it
restarted: a finished run's result is kept with the run, for as long as the organisation keeps conversations. A
run that a server restart stopped is continued by another server, and the `GET` answers with the run that continues
it (its own `run_id`). Only runs of the deployment whose key you send are found (signed in with OAuth, only your
own); any other id answers `404`.

### Be called back when a run ends

Instead of asking, a caller can be told: add `"callback_url": "https://…"` to the run request (`runs`, `runs/wait`,
`runs/stream`, or a `resume`). When the run ends (finished, paused for a person, failed or stopped), AgentCanvas
sends that address a `POST` with the run's result as `GET …/runs/{run_id}` gives it, plus `"type": "run.finished"`
and `deployment_id`:

```json
{"type": "run.finished", "deployment_id": "dep_…", "run_id": "run_…", "thread_id": "th_…",
 "status": "success", "output": {…}, "interrupts": [], "error": null}
```

- **First create the deployment's callback secret:** **Callback secret** next to the deployment on the **Versions**
  tab (or `POST /v1/deployments/{id}/callback-secret`, as a builder). It is shown once; creating a new one replaces
  it at once, also for callbacks not sent yet. A run that asks for a callback before the deployment has a secret is
  refused (`422`).
- **Check the signature** before you trust a callback, exactly as for [incoming webhooks](#triggers):
  `X-AgentCanvas-Signature` is `sha256=` and the hex HMAC-SHA256, keyed with the callback secret, of the
  `X-AgentCanvas-Timestamp` value, a `.` and the raw body. Refuse a timestamp more than 5 minutes old.
- **Only `https://` addresses** are accepted, never a link-local or cloud metadata address, and on the hosted
  edition only public ones (checked again on every delivery).
- **At least once:** an answer other than `2xx` (or none within 10 seconds) is tried again after about 30 seconds,
  1.5 minutes, 4.5 minutes and so on, up to an hour apart, 8 times in all (about 3 hours). The same callback can
  arrive twice: `X-AgentCanvas-Delivery` (the run id) tells repeats apart. Redirects are not followed.
- Builders see each run's callback (sent, still being tried, or given up, with the receiver's last answer) in
  `GET /v1/runs/{run_id}` under `callback`.

### Connect a CopilotKit-style front end (AG-UI)

`POST /v1/deployments/{id}/ag-ui` speaks [AG-UI](https://docs.ag-ui.com) 1.0, the protocol that CopilotKit and
other agent front ends use to talk to an agent. The request is an AG-UI `RunAgentInput`, sent with the deployment's
key (or, with [sign-in](#sign-in-with-oauth) on, a person's access token for the endpoint's address); the answer is a
stream of AG-UI events (Server-Sent Events). It runs the published version like any other
deployment call: the run shows in **Runs**, counts for rate limits, spending caps and online evaluators, and is
marked as AI-generated (`X-AI-Generated`, and `generated_by` in `RUN_FINISHED`'s `metadata.agentcanvas`).

Keep the key on your server: point CopilotKit's runtime (or your own backend) at the endpoint with an AG-UI
`HttpAgent`, and let the browser talk to your backend, never to AgentCanvas with the key.

```ts
import { HttpAgent } from "@ag-ui/client";

const agent = new HttpAgent({
  url: "https://agentcanvas.example.com/api/v1/deployments/<deployment id>/ag-ui",
  headers: { Authorization: `Bearer ${process.env.AGENTCANVAS_KEY}` },
});
// CopilotKit: new CopilotRuntime({ agents: { support: agent } })
```

**What goes in.**

- `threadId` is the conversation. A new id (1-128 letters, digits, `.`, `_`, `:` or `-`) starts one, an id this
  deployment already used continues it. An id taken by another conversation is refused (`409`), without saying
  whose; start with another id.
- `messages`: a chat workflow (one whose input is a message list) gets the whole conversation the first time, then
  only the new user messages (the thread keeps the rest). Any other workflow gets the newest user message in its
  text input (the one a share link would use). Only the text of a message is taken for now, not images or files.
- `state` sets the workflow's other input fields (unknown names are ignored).
- `tools` are tools your front end runs itself, for example in the person's browser. Every **AI Agent** and **Deep
  Agent** block of the workflow is offered them beside its own tools (its own tool of the same name wins). When the
  model calls one, the run ends as a success with the call in `pendingToolCallIds`; your front end runs the tool and
  sends the result as a tool message (`role: "tool"`, the call's `toolCallId`) on the next run of the thread, which
  continues the agent with it. CopilotKit's front-end actions do this for you. When the model calls several at once,
  answer them all in the same request. A tool call nobody answers expires like an approval
  (`AGENTCANVAS_APPROVAL_EXPIRY_DAYS`, 90 days), and the conversation then takes new messages again. Tools need the
  LangGraph framework; on another framework they are not offered and the stream carries a `CUSTOM` `ac.warning`.
- `context` entries are shown to the agents as information from your application (data, not instructions).
- `forwardedProps` may carry `user_id` (your end user, as in the deployment API's `user_id`) and `metadata` (run
  labels, up to 20 strings). Anything else in it is ignored.

**What comes out.** `RUN_STARTED` (with `protocolVersion` `1.0`; `metadata.agentcanvas.run_id` is the run's id in
AgentCanvas, also in the `x-run-id` header), then:

| Events | From |
|---|---|
| `STEP_STARTED` / `STEP_FINISHED` | each step of the workflow |
| `TEXT_MESSAGE_START` / `CONTENT` / `END` | each reply of the AI steps that give the answer (those that end the run), as the model writes it, or in one piece when it does not stream. A workflow with a safety block streams nothing and sends its final, checked answer as one message at the end; so does a workflow without AI steps |
| `TOOL_CALL_START` / `ARGS` / `END` / `RESULT` | the tool calls of those steps, and the calls of your front-end tools (without a result: yours to give) |
| `REASONING_*` | the model's thinking, when it streams it |
| `STATE_SNAPSHOT` / `STATE_DELTA` | the workflow's input and output fields (never its private ones, nor the message list, which comes as messages) |
| `CUSTOM` | warnings, a wait for a run slot (`ac.queued`) and your workflow's own custom events |
| `RUN_FINISHED` / `RUN_ERROR` | the end: the run's output (minus the messages) in `result`, the tokens used per model in `usage` |

**Pauses.** When the run pauses for a person (an approval, a question, a tool approval), `RUN_FINISHED` has the
`interrupt` outcome with one interrupt per pause: `id`, `reason` (`approval`, `tool_approval` or `input`), a
`message` for the person and a `responseSchema` for the answer. Continue the thread with a `resume` entry for every
interrupt (all of them in one request: one left out is refused with `422`, and a new message is refused while the
run waits):

```json
{"threadId": "th-42", "runId": "r-2", "messages": [],
 "resume": [{"interruptId": "<id>", "status": "resolved", "payload": {"approve": true, "comment": "ok"}}]}
```

An approval takes `{"approve": true | false}` (or just `true` / `false`); `status: "cancelled"` rejects it. A
question takes its answer (`{"answer": "…"}`, or the form's fields when it asks for several). A step that names who
decides it is refused (`403`) unless it lets the caller answer too ([Who may decide](run-and-debug.md#who-may-decide));
its interrupt says so (`metadata.agentcanvas.decided_in_agentcanvas`), and a person decides it in the Inbox. Every
answer given here is in the audit log (action `api_ag_ui`, the interrupt ids and a fingerprint of the answers); plain
runs are not, like the other run calls.

Through the web server (production setups) the same paths are under `/api/`. The public API is rate-limited per
key, per webhook and per project (`429` with `Retry-After` when exceeded). Errors, versioning and where to get the
full OpenAPI reference (production included): [API reference](api.md).

## Share as an app or a chat widget

Once a workflow is published, people who are not developers can use it from a link. On the **Versions** tab, open
**Share as an app or a chat widget** under the deployment.

1. Tick **Anyone with the link can chat with this deployment**. Sharing starts switched off.
2. Give the page a **Title**, a **Greeting** (the first thing visitors read) and up to six **Suggested questions**
   (one per line; visitors click one to start). Press **Save**.
   The **AI notice** under the messages tells visitors they are chatting with an AI: by default *You are chatting
   with an AI assistant. Answers can be wrong.*, or, when the deployment's [AI-system record](#ai-system-record) says
   its end users are only your own staff, *You are using an AI assistant. Answers can be wrong: check them before you
   rely on them.* It is always shown, on the page and in the chat widget. You can reword it (up to 200 characters,
   for example in your visitors' language) but not remove it; the audit log keeps each wording you save.
3. **Copy link** and send it to people, or **Open the page** to try it yourself. The page works on phones and
   follows the visitor's light or dark setting. Each message, the greeting and the message box take the direction
   of their own text, so Arabic or Hebrew reads right to left; the page's own buttons and notes are in English.

Visitors need no account. Each message they send runs the published version, and the answer streams in as it is
written (when the workflow has a safety block, the answer appears once it has passed the checks). **New
conversation** starts over. Thumbs up and down go to the run's feedback, like feedback sent through the API (a
thumbs-down can put the run in an annotation queue). If the workflow asks a question, the visitor answers it in the
chat. Approvals are not the visitor's to give: the chat says the conversation waits for approval, and it waits in
your inbox. Nor is a question whose step names who answers it, unless that step lets the caller answer too. A run
that reaches a [Wait](widgets.md#wait) step tells the visitor it does the rest on its own later; that part does not
appear in the chat (read it on the Runs page). Each visitor's address has its own rate limit (`AGENTCANVAS_RATE_LIMIT_PER_SHARE_CLIENT`, 30 a minute by default).

**Files.** When the published version has a *file* or *image* input ([File and image fields](canvas.md#file-and-image-fields)),
the chat page shows an **Attach a file** box above the message line (drag and drop works too; an image shows a
preview). The visitor can send a file with or without a message. Files are limited like uploads in the editor
(10 MB, images, PDF, Word and text files, or only images when the input says so), belong to the visitor who sent them
(at most 30 per link), and are kept as long as conversations. Nobody can download them through the link.

**Put a chat button on your website.** Paste the snippet shown under **Chat widget** before `</body>` on any page:

```html
<script src="https://your-agentcanvas.example.com/widget.js" data-share="sh_…" async></script>
```

A round chat button appears in the bottom-right corner; it opens the same chat in a small panel, with the link's AI
notice under the message box. Optional attributes: `data-label` (the button's name, shown on hover and read by screen
readers; default *AI assistant*), `data-position="left"` and `data-color` (any CSS colour).

**Talk to the widget from your page.** The widget takes commands and reports what happens with `window.postMessage`,
so your page can open the chat from its own button, start it with a question, or react to an answer. Every message
is an object with `agentcanvas: 1` and a `type`; post to your own window (`location.origin`):

| You send | What happens |
|---|---|
| `{ agentcanvas: 1, type: "open" }` (optional `text`) | Opens the chat (and puts `text` in the message box) |
| `{ agentcanvas: 1, type: "close" }` | Closes it |
| `{ agentcanvas: 1, type: "prefill", text: "…" }` | Opens the chat and puts `text` in the message box; the visitor still presses Send |

| You receive | When |
|---|---|
| `{ agentcanvas: 1, type: "ready" }` | The chat page has loaded (after the first open) |
| `{ agentcanvas: 1, type: "reply", text, runId, threadId, question }` | An answer arrived (`question: true`: the assistant asks the visitor something) |
| `{ agentcanvas: 1, type: "opened" }` / `"closed"` | The chat was opened or closed, by a visitor or by a command |

```html
<button id="help">Ask about my order</button>
<script>
  document.getElementById("help").onclick = () =>
    window.postMessage({ agentcanvas: 1, type: "prefill", text: "Where is my order?" }, location.origin);

  window.addEventListener("message", (e) => {
    if (e.source !== window || e.origin !== location.origin || e.data?.agentcanvas !== 1) return; // only the widget
    if (e.data.type === "reply") console.log("The assistant said:", e.data.text);
  });
</script>
```

Check `event.source` and `event.origin` as above: other frames on your page can post messages too. The widget acts only
on commands from its own page, never from another frame. Inside the chat frame, **Sites that may show the chat widget**
(below) decide who may talk to it: a `prefill` from a site that is not on your list is ignored, and the chat page
sends its events only to a site on the list. If you build the iframe yourself instead of using `widget.js`, send
`{ agentcanvas: 1, type: "prefill", text }` to the frame (`frame.contentWindow.postMessage(message, "<chat origin>")`)
and listen for the `ready` and `reply` messages from it; the same rules apply.

**Which input gets the message.** The visitor's message goes to the workflow's text input: one named like
`message`, `question`, `query` or `input` if there is one, else a chat message list (`messages`), else its only
text input. The share box says which one it picked; when the workflow has more than one, choose another under
**Messages go to** (it is saved at once). The answer shown is the last assistant message, else an output
field such as `answer`, `reply` or `response`, else the only text output.

**Keep in mind.**

- The link is the key. Anyone who has it can chat, and their runs count against the deployment's usage limits.
  Share it only with the people it is meant for. The page never shows your workflow, its keys or its secrets.
- **New link** makes a new address. The old link stops working at once, and so do conversations started on it.
  Switching sharing off, or unpublishing the deployment, closes the page too. Unpublishing also revokes the link
  and ends its conversations, like the key: after publishing again, switch sharing on and hand out the new link.
- Visitors only ever see their own conversations. Each browser gets its own random visitor id; you see their runs
  in **Runs**, with the end user `share:<deployment>:<visitor>` (useful for per-user memory).
- **Sites that may show the chat widget.** Until you list any, every website can put the chat page in a frame (the
  widget works anywhere). List your sites (`https://www.example.com`, or `https://*.example.com` for all its
  subdomains) and only those sites, plus this app, can frame it; others see a blocked frame. The link itself still
  opens for anyone who has it.
- **Reloading keeps the conversation.** The chat page shows a visitor's latest conversation again after a reload and
  carries on in the same thread (a question the assistant was waiting on is still open). An answer still being
  written at the reload appears when it is ready (the page checks every 2 seconds, for up to 3 minutes); the checks
  leave a visitor who scrolled up to re-read where they are. It is kept on the server
  under the visitor's id, which their browser remembers, and goes when conversations expire (Settings › Organisation).
  **New conversation** closes it for good; a new link (**New link**) ends every saved conversation. A visitor who
  clears their browser data or uses another browser starts afresh; you still see the earlier runs in **Runs**.

## Triggers

On the Versions tab:

- **+ Webhook** creates a URL (`POST /v1/hooks/{trigger_id}`) that starts a run. Without a mapping the request
  body is the input; a mapping builds the input from templates over the body, e.g.
  `{"ticket": "{{ body.issue.title }}"}`.
  Senders sign each request: header `X-AgentCanvas-Timestamp: <unix seconds>` and
  `X-AgentCanvas-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body>` using the trigger's secret.
  Requests older than five minutes are refused.
- **+ Cron** runs the workflow on a 5-field schedule (minute hour day month weekday, e.g. `*/5 9-17 * * 1-5` or
  `0 8 * * MON-FRI`; `@hourly`, `@daily`, `@weekly`, `@monthly` and `@yearly` work too) with a fixed **Cron input
  (JSON)**. The schedule is read in the **time zone** next to it (an IANA name such as `Europe/Berlin`; the field
  starts with your browser's, and `UTC` is used when none is sent), so `0 8 * * *` stays 08:00 there through
  daylight saving changes. When the clocks go forward, a time that does not happen that day does not fire; when
  they go back, a set time in the repeated hour fires once (a schedule that runs every hour keeps running through
  both passes). Each scheduled minute fires once, however many API replicas run. When a scheduled run cannot
  start (its deployment was unpublished, a security check refused it), the trigger shows why and how many times in
  a row, until a run starts again. An occurrence missed while no server
  was running (a restart or an outage at that minute) fires once when one is back, if it is less than an hour old
  (the operator's `AGENTCANVAS_CRON_CATCHUP_S`); older ones are skipped, as is anything due while the trigger was
  switched off.

A trigger runs its deployment's pinned version when it is attached to one, otherwise the draft (the panel says
which). For starting
workflows from your own domain events, see [Events](workspace.md#events).
