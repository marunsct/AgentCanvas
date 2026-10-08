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
