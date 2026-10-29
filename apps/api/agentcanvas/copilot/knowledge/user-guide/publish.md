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
always pinned to a version. Editing the draft never changes what callers get until you publish again.

Publishing first runs a checklist, and refuses on a failure:

| Check | Passes when |
|---|---|
| validation | the workflow has no errors |
| policies | every organisation policy holds |
| secrets | every `{"$secret": …}` reference exists |
| tests | the saved test cases pass |
| contract | no breaking change against the live version (or it is the first publish) |
| release gate | the evaluation meets the environment's gate, if one is set |

The deployment's **API key** is shown **once**, when the deployment is created. Store it safely. **Rotate key**
issues a new one; **Unpublish** removes the deployment.

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
 "output": {"messages": [ … ]}, "interrupts": [], "error": null}
```

| Endpoint | Use |
|---|---|
| `POST /v1/deployments/{id}/runs/wait` | Run and wait for the result. |
| `POST /v1/deployments/{id}/runs/stream` | Run and receive Server-Sent Events as it goes. |
| `POST /v1/deployments/{id}/threads/{thread_id}/resume` | Answer a pause: body `{"value": true}` (and `interrupt_id` when several are open). |
| `POST /v1/deployments/{id}/runs/{run_id}/feedback` | Send `{"rating": "up" \| "down", "comment": "…"}`. |

Request fields: `input` (the workflow's input channels), `thread_id` (continue a conversation), `context`, and
`user_id` (your end user, used for per-user memory). When `status` is `interrupted`, `interrupts` says what the
run is waiting for.

Through the web server (production setups) the same paths are under `/api/`. The public API is rate-limited per
key, per webhook and per project (`429` with `Retry-After` when exceeded). Full, interactive API reference:
`http://localhost:8000/docs` in development.

## Share as an app or a chat widget

Once a workflow is published, people who are not developers can use it from a link. On the **Versions** tab, open
**Share as an app or a chat widget** under the deployment.

1. Tick **Anyone with the link can chat with this deployment**. Sharing starts switched off.
2. Give the page a **Title**, a **Greeting** (the first thing visitors read) and up to six **Suggested questions**
   (one per line; visitors click one to start). Press **Save**.
3. **Copy link** and send it to people, or **Open the page** to try it yourself. The page works on phones and
   follows the visitor's light or dark setting.

Visitors need no account. Each message they send runs the published version, and the answer streams in as it is
written (when the workflow has a safety block, the answer appears once it has passed the checks). **New
conversation** starts over. Thumbs up and down go to the run's feedback, like feedback sent through the API (a
thumbs-down can put the run in an annotation queue). If the workflow asks a question, the visitor answers it in the
chat. Approvals are not the visitor's to give: the chat says the conversation waits for approval, and it waits in
your inbox. Each visitor's address has its own rate limit (`AGENTCANVAS_RATE_LIMIT_PER_SHARE_CLIENT`, 30 a minute by default).

**Put a chat button on your website.** Paste the snippet shown under **Chat widget** before `</body>` on any page:

```html
<script src="https://your-agentcanvas.example.com/widget.js" data-share="sh_…" async></script>
```

A round chat button appears in the bottom-right corner; it opens the same chat in a small panel. Optional
attributes: `data-label` (the button's name), `data-position="left"` and `data-color` (any CSS colour).

**Which input gets the message.** The visitor's message goes to the workflow's text input: one named like
`message`, `question`, `query` or `input` if there is one, else a chat message list (`messages`), else its only
text input. The share box says which one it picked. The answer shown is the last assistant message, else an output
field such as `answer`, `reply` or `response`, else the only text output.

**Keep in mind.**

- The link is the key. Anyone who has it can chat, and their runs count against the deployment's usage limits.
  Share it only with the people it is meant for. The page never shows your workflow, its keys or its secrets.
- **New link** makes a new address. The old link stops working at once, and so do conversations started on it.
  Switching sharing off, or unpublishing the deployment, closes the page too.
- Visitors only ever see their own conversations. Each browser gets its own random visitor id; you see their runs
  in **Runs**, with the end user `share:<deployment>:<visitor>` (useful for per-user memory).
- **Sites that may show the chat widget.** Until you list any, every website can put the chat page in a frame (the
  widget works anywhere). List your sites (`https://www.example.com`, or `https://*.example.com` for all its
  subdomains) and only those sites, plus this app, can frame it; others see a blocked frame. The link itself still
  opens for anyone who has it.
- Messages a visitor sends are not kept in their browser: reloading the page starts a new conversation.

## Triggers

On the Versions tab:

- **+ Webhook** creates a URL (`POST /v1/hooks/{trigger_id}`) that starts a run. Without a mapping the request
  body is the input; a mapping builds the input from templates over the body, e.g.
  `{"ticket": "{{ body.issue.title }}"}`.
  Senders sign each request: header `X-AgentCanvas-Timestamp: <unix seconds>` and
  `X-AgentCanvas-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + body>` using the trigger's secret.
  Requests older than five minutes are refused.
- **+ Cron** runs the workflow on a 5-field schedule in UTC (e.g. `*/5 9-17 * * 1-5`) with a fixed **Cron input
  (JSON)**. Each scheduled minute fires once, however many API replicas run.

A trigger runs its deployment's pinned version when it is attached to one, otherwise the draft (the panel says
which). For starting
workflows from your own domain events, see [Events](workspace.md#events).
