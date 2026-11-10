# Decisions made without asking

Judgment calls made while hardening AgentCanvas on its own, newest first. Each has what was decided, why, and how to
reverse it. Read it when you have a moment; tell me (or edit the entry) if you disagree and I will change course.
Merging, deleting branches and anything outward-facing stay with you: work is delivered as branches and pull requests.

**Standing guidelines I follow** (from the product, `HANDOVER.md` and what you asked for):
- Fail closed and say why: a refused request, a warning, a failing check, never a silent skip.
- One concern per commit; every fix lands with a test that fails without it; the suites run on Postgres, not only SQLite.
- No version bump or tag (yours to decide); `CHANGELOG.md` "Unreleased" and `HARDENING.md` are kept current.
- Prefer checks that grow with the product (driven by the OpenAPI document or the template list) over lists to maintain.

## 2026-11-10 (leftovers)

### Judgment calls in the fourth wave (delete retries, withdrawn items, generated types, OAuth everywhere, approval links)
- Retries are bounded at 4 attempts (about 1 s) and then fail closed with 503 rather than wait longer; raise `ATTEMPTS`
  in `db/retry.py` if operators see `db.retry_gave_up`.
- Only LangGraph (the framework whose saved state holds every pause) withdraws at the end of a run; other frameworks'
  items are left as they were. A Wait step left behind is "cancelled", not "withdrawn", as before. Downgrading the
  migration turns withdrawn items into expired ones.
- Output models whose defaulted fields are always sent use json_schema_serialization_defaults_required instead of losing
  their defaults, so models built step by step keep working; remove the config to make them optional in the schema
  again.
- The deployment API is one OAuth resource at the deployment's address rather than one per route, and reading takes
  agentcanvas:deployment:read as an alternative to the configured scopes; to give each route its own audience, return
  the route's address from _address in api/v1/deployments.py. Signed-in callers may not ask for callbacks; to allow it,
  drop the check in _callback_url.
- One reminder per deadline (an escalated item gets a second before it expires) rather than strictly one per item; to
  send only one, drop reminded_at=None from notifier.escalate. Reminders are on by default because they only go to
  organisations that already switched notifications on.
- Off by default per organisation because a personal link decides without a sign-in; channel links never decide
  directly. A link is single-use through the item's own one-time claim rather than a used-token table; add a table keyed
  by a nonce in the token if a link must stay spent even when its decision is rolled back (R-4). Links last at most 7
  days (approval_links.MAX_DAYS); reminders carry fresh ones.

## 2026-11-10 (the rest of the roadmap)

### E-10 to E-13 and the re-scoped F items, built together
Per-project access, OAuth for deployment MCP and A2A, Bedrock and Vertex AI, the AI-system record, LangGraph strict
resume, AG-UI 1.0, the Wait block, Copilot run search and the weekly digest, and stdio MCP servers in the sandbox were
built in parallel and merged on `claude/hardening-9`. What is left of each is in `hardening.md`.

### Three ways of answering a pause, one rule
Strict resume (an answer names its pause), AG-UI (several answers at once, and tool calls the caller's application
runs, which have no Inbox item) and the Wait block (only its time or event ends it) all changed how the runner marks a
pause answered. They were merged into one `_decide_interrupts`: an answer is matched to its Inbox item through
`approvers.answered`; an AG-UI tool call passes only when the paused run asked for exactly it; a Wait step refuses any
answer but its own wake; an answer naming nothing goes to the one other pause beside a Wait step. *Reverse:* the
function in `runtime/runner.py`.

### Diagnostic codes are claimed once
The cloud models and the Wait block both took E024 and W028; the Wait block's became E097 and W096.

### Judgment calls inside these items

- A deployment key still acts for the deployment's owner and may continue any end user's conversation, signed-in ones
  included; only signed-in callers are bound to their own. To bind keys too, pass the thread's owner check for key
  callers in own_thread.
- A configured audience is accepted besides the endpoint's address (not instead), so RFC 8707 clients and fixed-audience
  IdPs (Entra, Okta) both work; drop the resource from `audiences` in _signed_in to accept only the configured one.
- The deployment API (runs/wait and the rest) still takes keys only; to accept tokens there, pass an oauth route from
  its callers of _caller.
- The fields live on the deployment (one record per environment), not the workflow, since staging and prod can differ in
  risk and audience; the evidence is read at request time rather than snapshotted, so it cannot go stale. To snapshot it
  at publish instead, store build()'s output on the version.
- HTML rather than a server-made PDF, so the API needs no PDF library and the browser's print gives the PDF; add a
  renderer in system_record_doc.render if a PDF file is required.
- LangGraph's strict resume is enabled through its config key `__pregel_strict_resume` (imported from
  langgraph._internal, no public name in 1.2.20); to reverse, drop that line in the LangGraph adapter.
- An unanswered front-end tool call expires after AGENTCANVAS_APPROVAL_EXPIRY_DAYS rather than a setting of its own;
  give it one in services/retention.py `expire_tool_calls` if operators want a shorter wait.
- AG-UI events echo the caller's threadId and runId (AgentCanvas's run id is in RUN_STARTED/RUN_FINISHED metadata and
  `x-run-id`); only the answer steps' text and tool calls are streamed, like share links. To stream every step, pass all
  AI steps as `shown` in api/v1/agui.py.
- Vertex AI prices are recorded at the regional or multi-region endpoint's price (global is 10% less), because the
  region is a block setting and not part of the model id: budgets err on the safe side. Reverse by pricing by region in
  runner.price.
- The hosted edition never uses the server's own AWS or Google Cloud identity for a tenant's model; self-hosted servers
  do, for every project (like a provider key in the server's environment). Reverse in nodes.public_only_args.
- A Wait step can never be answered by a person or a caller (409); only its time or its event ends it. A builder who
  wants to skip a wait in a draft has to Stop it and run again. Reverse by letting builders resume a draft
  conversation's timer with ResumeSpec(wake=True) from a new endpoint.
- Digest notifications carry counts, spend, latency, failure-group sizes and workflow names, not the errors' text (an
  error can hold what a run was given), like approval notices never carry what a run asks; to send the reasons too, add
  them in DigestMessage.lines and payload.
- The connector sandbox is kept per run by default and per conversation only when the block asks for it, because a
  browser session is state the next run may not expect; a conversation continued on another API server starts a fresh
  program (the sessions are per server). Reverse by changing the `session` default in mcp.server.json.
- A hosted connector matches a gallery entry by command, leading flags and exact package version, so the image and the
  gallery cannot drift apart silently; to allow more, mark the entry sandboxable and install it in the browser image.
- Only exact `npx package@version` commands are mapped to the installed copy, so a gallery bump without an image rebuild
  fails visibly instead of running another version; uvx connectors are not installed in the image yet.
- A project role is not capped by the organisation role (an organisation viewer can be one project's builder or admin,
  as in GitHub and LangSmith), so a project admin is possible without being an organisation admin; an API token stays
  capped at its own role everywhere. With private projects off, a binding only raises a role. To cap instead, make
  Access.role_in return the weaker of the binding and member_role.
- A principal is two nullable foreign keys (user_id, group_id, exactly one set) rather than a kind plus a free id, so
  deleting a user or a SCIM group deletes its bindings in the database.

## 2026-11-10 (enterprise controls)

### The enterprise controls came before the remaining product items
The launch review's research found that what a buyer's security review asks for (SIEM delivery, tamper-evident
audit, offboarding and data-subject rights, credential policy, spending caps, approval routing, AI Act disclosure,
MCP tool pinning, durable results for API callers) was missing from the roadmap, so E-1 to E-9 and the re-planned F-1
were built before F-8 onward. Each first version is complete and tested; what is left of each is listed in
`hardening.md`.

### Every audit entry has one write path
The audit outbox and the hash chain only hold if every entry goes through `AuditRepo.add`. Code written at the same
time (spending-cap alerts, approval escalations, MCP tool drift, an organisation's purge, an export's download) wrote
entries directly or through the sink the outbox replaced; they were moved onto it, and an entry that must join its
caller's transaction passes `commit=False`. *Reverse:* none intended; a new direct write breaks the chain (the verify
endpoint flags it).

### An MCP request state is bound to the key used
F-1 bound a pause's `requestState` to the deployment's single key, but E-8 gave deployments several keys. The state
is now bound to the hash of the key the call was made with, so another key of the deployment, or the same key once
revoked, cannot answer the pause. *Reverse:* `_presented_key` in `api/v1/protocols.py`.

### Judgment calls inside the enterprise controls

- Both clients ship in the default image so the documented setting works everywhere; the KMS build argument leaves them
  out for slimmer builds. To reverse, default KMS to "" and document the build argument as required for cloud keys.
- A run that called no model is still marked "ai" (models empty): over-marking an AI system's output is safe, missing a
  mark is not. To narrow it, mark only when models is non-empty in services/disclosure.py.
- The notice cannot be switched off, even by an organisation setting: it is the legal minimum for public chat. To allow
  switching it off, add an audited organisation setting and let ai_notice be empty only when it is on.
- Deployment.api_key_hash/key_prefix are kept (nullable, unused) for rolling upgrades instead of being dropped now; a
  later migration drops them. Personal API tokens may still make deployment keys (CI publishes with them), as they could
  publish before.
- Webhook triggers are not covered (their senders have no fixed addresses and their signature proves who sent them); the
  list is read from the database on each request rather than cached, so a change applies on every replica at once.
- A requirement is met by either list (acr in one, or amr in the other), not both; personal API tokens are exempt from
  required MFA (they are made from a sign-in that met it), to be revisited if questionnaires ask for more.
- Project caps are set by organisation admins only, since there are no project roles yet (E-10); a builder role for its
  own projects can be added with a ROLE_EXCEPTIONS entry once project admins exist.
- A run in flight carries on when the cap check cannot reach the database (logged as run.spend_cap_check_failed), as
  workflow budgets do; the start gate fails closed.
- Judges are counted but never stopped mid-scoring (they score runs, which the caps already stop); to stop them too,
  call SpendCaps.refuse in scorers' complete.
- Only deployment runs keep their output on the row (draft runs are read from their conversation); store it for every
  run by dropping the deployment_id check in runner._result_columns.
- Callbacks need a callback secret a builder creates (no secret is made at publish, so existing deployments are
  unchanged); to create one at publish instead, call callbacks.set_secret in DeploymentService.publish and return it in
  PublishOut.
- The `answer` tool is hidden from form-capable clients but still accepted when called, so a client that drops the
  capability keeps working; refuse it for them in _mcp_call to make forms mandatory.
- Roles match exactly (tick each role you mean) and there is no admin override, so a step limited to a group is not
  silently decided by an admin; escalation is the way out. The check applies to draft runs too, like the own-run policy.
  Drop the `approvers.check` calls to reverse.
- Draft runs a person starts in the editor are not announced unless their step names who decides them (the person is
  watching the run); remove the `quiet` condition in NotifierJob.announce to announce them too. A step that names nobody
  e-mails every reviewer, builder and admin (at most 100) unless the organisation lists team addresses.
- An item escalates once and then gets one more full expiry period before its run is cancelled; an item with its own
  escalation time does not expire before it escalates. Change `escalate` and the expiry query in retention.py to
  escalate repeatedly or shorten the second period.
- Versions published before pinning keep working: tools browsed then (no fingerprints) are pinned by name only, and a
  block never browsed offers the server's current tools with a warning on every run. Failing such runs instead would
  break existing deployments on upgrade; to make it strict, return an empty list from `mcp_pins` callers when it is
  None.
- E045 is an error only at publish and a note in the editor, so existing drafts keep running; making it an editor error
  would block draft runs of unbrowsed connectors.
- The organisation row is kept as a tombstone holding the deletion's audit entries, instead of a platform-level log;
  drop the row in the purge (and the two kept audit rows) to remove even that.
- Conversations a member started stay unless the admin asks for them to go, since they are often the project's records
  too; flip the default of `conversations` to change that.
- A deployment key may erase its own end users (it already holds their conversations), so customers can automate
  requests; remove the public route to make it admin-only.
- The archive lives in the database rather than on a volume, so replicas need no shared disk; move it to object storage
  if organisations grow past a few GB.
- The outbox stores one row per entry and destination and rebuilds the event from the audit row at delivery; a
  destination removed from the settings keeps its rows pending (alerted on) until an operator discards them, rather than
  having another replica with older settings drop them.
- The hash is a plain SHA-256 (no server key), so anyone can verify a chain from the rows alone; a full rewrite by
  someone with database access is caught by the forwarded copies, not by the chain. A keyed hash (HMAC with a server
  secret) could be added later as a second column.
- The one-day floor in audit_expire is fixed in the migration (a new migration changes it); it bounds what an attacker
  holding the API's role can delete, while forwarding to a SIEM keeps the copies.

## 2026-11-10 (launch review)

### A third deep review, aimed at launch
Eleven reviewers read the code, security, operations, data, web app, adapters and docs, and every finding went to a
second agent told to refute it; 104 of 107 survived and were fixed on `claude/hardening-7`, one concern per commit,
each with a test that fails without it. Two researchers checked the product against OWASP ASVS 5.0, the OWASP LLM and
Agentic Top 10s, NIST AI 600-1, ISO 42001, the EU AI Act and CRA, SOC 2 and EnterpriseReady, and against what
changed in MCP, A2A, AG-UI and the competing products; their recommendations re-shaped the roadmap in
`hardening.md` (the enterprise controls E-1 to E-9 now come before F-8 onward). The run queue's design changed in
the process: instead of each waiting run locking the organisation's row every second, one admission pass per server
and organisation takes the free slots, and a run waits at most `AGENTCANVAS_MAX_QUEUE_WAIT_S` (1 h); this replaces
the row-lock design described in the entry below.

### No licence was chosen
The repository has no LICENSE, so customers have no written right to run AgentCanvas or ship exported code. That is
a commercial decision for you; the user guide now says the terms are not set yet instead of promising rights
(`docs/user-guide/faq.md`, `security.md`). *Reverse:* add a LICENSE and replace those two passages.

### Judgment calls inside the fixes
Each was written in its commit as a `Decision:` line; collected here with how to reverse it.

- No retention sweep of checkpoints without a conversation row; the clear-on-first-run backstop covers reuse, and a
  sweep would need every framework to list its saved threads.
- Migrations hold a session-level advisory lock polled every 0.5 s instead of a transaction-level one, so a migration
  may commit midway (CREATE INDEX CONCURRENTLY); to reverse, build indexes inside the transaction and go back to
  pg_advisory_xact_lock.
- The archive keeps hourly totals for as long as the workflow exists, with no retention of its own; to cap it, delete
  rows older than the audit retention in the retention job.
- Chromadb's four advisories (no fix released) are accepted for CrewAI's environment because they concern chromadb's
  server, which AgentCanvas never runs; remove the --ignore-vuln lines in scripts/audit-deps.sh to fail on them again.
- The API's uv.lock gets no `exclude-newer`: every install is `uv sync --frozen` and updates come only through
  Dependabot (now with a cooldown), while an exclude-newer window would also block an urgent security fix from
  resolving. Add `[tool.uv] exclude-newer = "7 days"` to apps/api/pyproject.toml to change this.
- The supported versions (latest release and main only) and the response targets (acknowledge in 3 business days, assess
  in 10, fix critical/high in 30 days and the rest in 90) are proposals for the owner; change them in
  .github/SECURITY.md. GitHub's private vulnerability reporting must be switched on in the repository's settings (Code
  security) for the link to work.
- The licence is left to the owner; when one is added, replace the FAQ answer and the Compliance paragraph in
  docs/user-guide/security.md with its terms (the test then stops checking).
- Refused rather than run through the sandbox, because the framework object (an agent, a crew, options) cannot cross the
  sandbox's JSON boundary; designed agents (AI agent block "Designed for" a framework) are the isolated way. To reverse,
  run the step through SandboxRunner.exchange with a callable-only contract.
- 256 rather than Docker mode's 64, so libraries that start a thread per core keep working on large nodes; lower
  PROCESSES in sandbox/runners.py to tighten it.
- The same environment allowlist applies to parts in exported projects, since their model and tools also come from the
  host; widen WORKER_ENV in parts.py if an export's part needs more.
- A floor (pytest>=8) like every other export rather than an exact pin, since no export ships a lockfile for its Python
  test tools; to pin exactly, keep one static version table (scripts/framework_versions.py) and write it in every
  packager.
- Running runs now get AGENTCANVAS_DRAIN_GRACE_S counted from SIGTERM rather than after uvicorn's 30 s wait, so a long
  run is handed over sooner; raise the drain grace (and the grace period with it) to give runs longer, or move `await
  app.state.wind_down.begin()` back to only the lifespan's shutdown to restore the old timing.
- The version is behind sign-in (GET /v1/version), not on the public /healthz, so an anonymous caller cannot fingerprint
  the release; operators also have the metric, the log line and the labels. Add it to /healthz if a monitor needs it
  unauthenticated.
- The document is behind sign-in rather than public, which keeps S15's reason (no map for anonymous callers); the type
  URIs stay https://agentcanvas.dev/errors/* because clients match on them: moving them to a domain the project owns (or
  to URNs) is the owner's call and a breaking change. Operations without a description (about 110) are left for a
  separate pass.
- Limits are 1 s per render, 10 million characters of text and a million list items; they are constants at the top of
  the bounded-templates section of expr.py.
- A dedup value older than the window runs its handler again if the same value is delivered later; set
  AGENTCANVAS_EVENT_DEDUP_DAYS=0 to keep dedup values for the whole conversation retention instead.
- One server-wide expiry with a generous default rather than per organisation or per interrupt node; set
  AGENTCANVAS_APPROVAL_EXPIRY_DAYS=0 to restore approvals that wait forever.
- At most one late run per gap (the latest missed occurrence), like Kubernetes CronJob's startingDeadlineSeconds; set
  AGENTCANVAS_CRON_CATCHUP_S=0 for the old behaviour, or raise it for longer outages.
- In the repeated hour when clocks go back, a set time fires once (first pass) and an every-hour schedule fires in both
  passes, like Vixie cron; a skipped time does not fire (Kubernetes CronJob behaviour). Changing either is in
  Cron.fires_at.
- Scoring still waiting when a server stops is not resumed (no durable scoring queue); a durable queue would need a
  table and a worker, worth it only if dropped or lost scores show up in practice.
- Tokens made before a cap keep their expiry (they are listed for admins to revoke); shortening them silently could
  break running scripts. Clamp existing expiries in TokenService.authenticate to reverse.
- No new setting; the hosted edition alone stops the fallback. Add an operator flag in langsmith_sync.api_key to
  reverse.
- Allowed by default, so existing installations keep working; the dead `assignee_role` column is left for per-step
  approver roles later. Set the default to false in settings.py to make four-eyes the default.
- The log line is always on (no setting): it is the cheapest path into a SIEM, at one line per security change. Drop
  LogSink from sink_for to reverse.
- Loopback stays reachable with private networks allowed (local services on a desktop install); only link-local,
  unspecified and metadata addresses are refused. Add `ip.is_loopback` to is_metadata's callers to refuse it too.
- The maximum wait defaults to an hour; set AGENTCANVAS_MAX_QUEUE_WAIT_S=0 to wait without limit. No cap on the queue's
  length was added: a waiting run now costs no database work of its own.
- The `agentcanvas_runs_started_total` metric no longer has an `origin="handover"` value; a continued run counts under
  its own origin.
- Low-priority runs do not age into higher priority; the maximum queue wait (AGENTCANVAS_MAX_QUEUE_WAIT_S) bounds their
  wait instead. A release gate's overall 600 s limit still includes queue time.
- The header goes on every A2A and MCP call a run makes, also to servers outside AgentCanvas; it carries only the run id
  and a signature only this server can check. Remove `nesting.install` in api/main.py to stop sending it.

## 2026-11-10

### Run priority comes with a runs-at-once limit; nothing queues by default
A priority only means something when runs wait, so it ships with admission control: `AGENTCANVAS_MAX_CONCURRENT_RUNS`
(0, no limit, by default: nothing changes until an operator or an organisation sets one) and an organisation's own
lower value, like the rate limits. A run past the limit waits in the database, so every server shares one queue; on
Postgres the organisation's row is locked while a run takes a slot, so two servers cannot both take the last one. The
next slot goes to the highest priority, then the oldest run. Not built: fairness between projects (one busy project
can fill the organisation's queue) and per-trigger priorities; each is a field away once someone needs it.
Experiments and test cases default to low, people's runs and triggers to normal; the deployment API takes no
priority, so a key holder cannot jump the queue. A waiting run is "running" for its thread, the kill switch and a
person's stop, so none of them needed changing. *Reverse:* migration `b8e2f6a0c4d7` and `runtime/admission.py`.

### Docs: one place for users, one for contributors
The owner asked for the documents to be tidied without scattering. The user guide (`docs/user-guide/`) is for
people building workflows and operating a server; everything for contributors is under `docs/developer/`, whose
README is the map. Status lives only in `hardening.md`; the October handover, progress log, product plan, sprint plan
and old reviews moved to `docs/developer/history/` with a note saying where the current information is. The build
guide's deployment chapter was written for operators, so it became the user guide's *Operations reference*. Older
entries below still name the old paths (HARDENING.md, PLAN.md, HANDOVER.md) as they were when written.

### Decide with AI reads answers in one place
The question and the reading of the model's answer live in `agentcanvas/expr.py`, which every runtime and export
already carries, so the eleven frameworks decide alike; Mastra's TypeScript twin is checked against it on tricky
answers. The same decision is the *AI classification* evaluator, so a block and an evaluator never disagree about
what a model meant.

## 2026-11-09

### The failure summary groups comments; it does not ask a model
Each target's failures are grouped by rule (a run's error, or an evaluator's comment with numbers and quoted values
taken out) and written as one line, with no model call: it costs nothing, gives the same answer every time, and
needs no judge model or keys. Judges' free-text reasoning is grouped by evaluator only, with one reasoning shown,
because no two read alike. An AI-written analysis on top (AgentCore style) can come later, opt-in, if people want it.
Argument matchers: an expected call names only the arguments that matter (subset by default), so examples keep
passing when a tool gains an optional argument. *Reverse:* `failures()` in `services/experiments.py`.

### Decide with AI asks for JSON, and starts on LangGraph only
The decision is a JSON answer the step parses ({choice, confidence, reason}) rather than the provider's structured
output, so every model and the canvas's scripted replies work the same; a wrong or missing answer takes the default
path, and an optional minimum confidence does too. It reuses the path step's `cases`/`default` shape, so its outputs
are path dots everywhere without new canvas code. Only LangGraph runs it for now: the other ten frameworks refuse it
with P001 (their manifests list no `core.decide`) instead of running something different. Next: the other
frameworks' code generators, and the same decision as an evaluator kind. *Reverse:* delete
`registry/nodes/core.decide.json` and the two functions in `adapters/langchain/nodes.py`.

### Repeated runs: the pass rate stays per run, and repeats skip the answer cache
With k runs per example, the summary's `pass_rate` is still the share of runs that passed, so it compares with earlier
experiments; pass@k and pass^k are added beside it, and release gates keep reading the pass rate. A model block's answer
cache would hand every repeat the first answer, so repeated experiments run with it off. Compare treats an example
as passing only if all its runs passed (the cautious reading). Left for later in F-6: argument matchers for tool calls
and a written failure summary per experiment. *Reverse:* migration `a7d1e5b9c3f6`.

### The kill switch stops runs in flight; run priority waits for a run queue
**Stop all runs** marks runs in the database and each server stops its own at its next heartbeat (10 s), because a run
lives on the server that started it. It does not also refuse new runs: stopping deployments already exists, and a
switch that blocks everything until someone finds the "off" again is a second outage. Its reason is required and
audited. **Run priority is not built**: AgentCanvas starts every run at once (there is no queue or concurrency limit),
so a priority would change nothing until there is admission control. That is its own design (a per-organisation
concurrency limit, a queue shared by every server, fairness between projects); I would rather do it properly than add
a field that does nothing. *Reverse:* migration `f4a8c2e6b0d3` and `services/kill_switch.py`.

### Cards are cached for five minutes; the Server Card's name comes from the public host
A2A 1.0 (section 8.6) and the MCP Server Card rules ask for `Cache-Control` and an `ETag`; the Server Card example
uses an hour. I chose 300 seconds so a republished or stopped deployment's card is not stale for long. The Server
Card must have a reverse-DNS name, so it is the public host reversed (`com.example.agents/<tool>`), which changes if
the address does: set `AGENTCANVAS_PUBLIC_URL`. `application/a2a+json` is the media type of A2A's REST binding, which
we do not serve; the JSON-RPC endpoint keeps answering `application/json` and already reads requests sent with
`a2a+json`. *Reverse:* `CARD_MAX_AGE` in `api/v1/protocols.py`.

### The OpenAI client for Agno and Strands moves to 3.46.0
This replaces "stays at 2.54.0" below. OpenAI's httpx2 migration guide says an injected legacy httpx client still works
at runtime, and Agno 3.1.12 only passes on an `httpx.AsyncClient` (it ignores any other client), so Agno's scripted test
model keeps httpx; Strands', which builds its own `AsyncOpenAI`, now uses httpx2. Verified on 3.46.0: the Agno and
Strands conformance and flow tests (151), both smoke checks, and the tests of all 44 Agno, Strands and mixed exports.
*Watch:* OpenAI calls the legacy-client path temporary; when Agno accepts httpx2 clients, move its scripted model too.
*Reverse:* `make framework-bump PKG=openai VERSION=2.54.0` and put `import httpx` back in `STRANDS_MODELS`.

### Importer upkeep checked against real files, not a fresh Agent Builder export
A fresh Agent Builder export needs your OpenAI account, so I used what is public: Langflow's own 27 starter projects at
v1.13.0, n8n's 3.0 breaking-changes page, and 34 Agent Builder exports people committed to GitHub (read with `ast`, never
run; kept only in my scratchpad, not the repository). The fixtures in `tests/importers/` are written or trimmed by hand
in the same shape. Nodes with no counterpart still become placeholders: the notes now name the block to use instead
rather than mapping, say, a vector store to a knowledge base automatically (the documents are not in the file).
*Reverse:* nothing to reverse; if you can export one of your own Agent Builder workflows before 30 November, import
it once to be sure.

### Every agent framework on its latest release, with two held back
Read from each project's own releases and changelog (links in `make framework-versions`): LangGraph 1.2.20,
LangChain 1.4.9, Deep Agents 0.7.34, MCP SDK 2.3.0, Pydantic AI 2.66.0, Microsoft Agent Framework 1.25.0 (its OpenAI
client 1.18.1), Google ADK 2.14.0, CrewAI 1.15.39, Strands 1.64.0, Agno 3.1.12, LlamaIndex Workflows 2.26.1, Mastra
1.82.0, Claude Agent SDK 0.2.179; OpenAI Agents SDK 0.24.0 and Agent Spec 26.3.1 were already the latest.
- **LiteLLM stays at 1.105.7**: see the entry above; ADK 2.14 itself declares `<1.106.3`.
- **The OpenAI client for Agno and Strands stays at 2.54.0**: Strands 1.63 now allows 3.x and Agno has no upper
  bound, but openai 3 made httpx2 the default client and our scripted test models use httpx; LlamaIndex's OpenAI
  package still requires `<3`. Moving to 3 is one change for Agno and Strands together, with the scripted clients
  ported to httpx2. *Reverse:* raise `OPENAI_VERSION` (agno/codegen.py) and the two `openai==` pins.
- **CrewAI in the image runs on Python 3.13**: CrewAI declares `<3.14` but had been installed on the image's 3.14,
  which nothing tested (`make crewai` and CI use 3.13).
- **LangGraph's error-handler fix retired a workaround**: workflows with error routes stream tokens again.
- **The Copilot keeps the loop engine** (evaluation on LM Studio, see build guide page 14): Deep Agents passed the same share of cases but took 2.7 times as long, used more tokens and ran to its step limit. *Reverse:* merge `pending/copilot-deep-agents-default`.
- **Upgrades from now on**: Dependabot watches `infra/frameworks/requirements.txt` (one pull request per framework),
  `make framework-bump` moves a pin everywhere, and the procedure is build guide page 16.

## 2026-11-08

### The second review: how it was fixed
Seven read-only reviews found about 110 defects; six agents fixed them by area in worktrees and I merged their
commits, each with a test that fails without it. No AI inference ran (the Copilot evaluation stays pending). The
calls below are theirs or mine; each says how to reverse it.

### Security calls
- **SCIM and shared invitations:** a person invited by several organisations is one row until they sign in, so a
  SCIM rename or re-address of such a person is refused (409) rather than split into two users. *Reverse:* split the
  row in `ScimService._apply_user_attrs`.
- **Groups tables add new members only on a one-organisation server** (or with `AGENTCANVAS_SSO_GROUPS_ADD_MEMBERS`).
  Existing members and invitees are still changed by the table.
- **Service connection:** used only by runs no person starts, deployments' runs resumed from the Inbox, and admins'
  own runs. A builder can still publish to use it; that is inherent in publishing.
- **Entered OAuth credentials are bound to the MCP servers they were entered with.** Existing rows from before stay
  usable for the servers the organisation already connects to. No same-domain rule between connector and sign-in
  server: many real servers sign in through a provider on another domain.
- **Hosted edition:** other providers than OpenAI-compatible ones may not set their own address (refused, not
  pinned), and 6to4 and Teredo addresses are refused outright.
- **Deployment files:** runs started by a key, a share link or a webhook use only files given to that deployment; a
  schedule's own input (written by a builder) may still use the project's files.
- **Removing a member** deletes their tokens and connections in that organisation without asking the provider to
  revoke them (that would need a network call); suspension keeps them, since they are refused while suspended.

### Behaviour calls
- **Unscored examples fail a gate**, including examples an evaluator cannot score by design (an exact match with no
  reference): a gate must see every example scored.
- **A judge score outside 0..1 is refused**, not rescaled: the prompt asks for 0..1.
- **Copilot test runs stub remote agent steps** instead of asking about every new step with side effects (that would
  block testing any new AI step).
- **The fine-tuning export skips runs** with unanswered tool calls or clipped text rather than inventing tool messages.
- **Anthropic refuses sampling for every model** since API version 2026-10-21 (SDK 2.x): a temperature on any Claude
  model is dropped, with W020 saying so.
- **Budgets in exports:** the LangGraph deploy kit enforces per-run token, cost and time limits; everything else is
  listed in GAPS.md and warned about (W026), which also shows for people who never export.
- **New models without verified prices** are listed without them (W015: cost unknown) rather than guessed.
- **Git sync deletes only what an earlier push wrote** (`.agentcanvas-files`); a folder without that file is never
  pruned, so a stale file someone else added stays.
- **Read-only API container in production compose**, with `API_READ_ONLY=false` as the way out until the image stage
  confirms nothing writes elsewhere. Dumps now keep table owners; a hand restore elsewhere needs `--no-owner --no-acl`.
- **The "?" key opens the shortcuts page** (no overlay); queued and cancelled experiments are neutral, not red.

## 2026-10-30

### Closing the open items: what was built, and what was left
Built: OCR (Tesseract; the Docker image now includes it), the retaken guide screenshots, `infra/schema.sql` regenerated,
the Spending page by deployment, a trigger warning for per-person connectors, hand-entered OAuth client credentials,
file upload with a deployment key, scripted multi-turn red teaming, an automated accessibility scan with its fixes, and
broader cross-organisation tests. Left, with the reason in `HARDENING.md`: anything that needs a paid model or a
service account (the Copilot score on a hosted model, the real `ant` and GitHub round trips), audio and video ports,
live co-editing, OAuth in exported code, and an interactive attacker model.

### OCR: pypdfium2 renders, Tesseract reads, nothing else
Scanned PDF pages are rendered with pypdfium2 (a wheel with its own PDFium, no system library) and read by the
`tesseract` program, called with fixed arguments and the picture on standard input. Only pages with under 20 characters
of text are read, so a text PDF costs nothing. `AGENTCANVAS_OCR=auto` uses Tesseract when it is on the path, so the
feature switches on by installing it; the image installs English, other languages are a build argument. *Reverse:*
`AGENTCANVAS_OCR=off`. On this machine Tesseract and Ollama were installed under `~/.local` (no root here), not
system-wide.

### Entered OAuth credentials win over a metadata document
An admin who types a client id for a sign-in server wants that one used, even when the server also takes metadata
documents. They are stored in the table dynamic registration already used, so no migration. The secret is encrypted with
the organisation's key and never returned.

### The trigger warning is on the trigger, not in the validator
The validator sees only the workflow, and schedules and webhooks are stored beside it. The trigger endpoints return
`warnings` instead, shown in the editor. *Reverse:* delete `TriggerService.warnings`.

### Colours: the light theme's tokens got darker
The accessibility scan failed text and status colours at contrast ratios of 2.6 to 4.2. Muted text, the accent, the run
blue, ok green, warn amber and error red are darker in the light theme; the dark theme only lightened its muted text.
Buttons look the same at a glance. *Reverse:* the values are the first block of `src/styles.css`; the scan
(`e2e/axe.spec.ts`) tells you what a change breaks.

### Image CI no longer clashes with a running stack
The compose files pin two internal subnets (the API trusts forwarded addresses from one of them). `scripts/ci-images.sh`
now gives its two stacks subnets of their own, so it runs beside `make docker-up`. Running it found two real bugs, fixed
the same day: the API failed to start when neither a public URL nor a CORS origin was set (the production compose files
set neither), and the template export did not give a template its own workspace code.

## 2026-10-29

### Observability and presence: what they do and do not do
Runs keep up to 2,000 compact events (long text clipped) in `run.events` for the trajectory tree. Run search masks
project secrets before embedding; with a hosted embedding model the masked text goes to that provider (the docs say so).
Red teaming is an experiment option, not just an evaluator kind, because an evaluator cannot generate inputs; without a
judge model, "nothing leaked" counts as a hold for the first three categories and off-topic needs a refusal; multi-turn
red teaming was not built on 2026-10-29 (a scripted multi-turn category was added on 2026-10-30; an interactive attacker is not). The fine-tuning export approximates a conversation for multi-step pipelines (the final model
step with the run's input as the user message), and the OpenAI `tools` list is inferred from observed calls: check it
before uploading. Presence is database-backed (works across replicas); each open stream polls every 2 seconds. It shows
who is in the workflow and a "saved a newer version" notice; it is not live co-editing (no merging of simultaneous edits).

### Governance and per-person connections: scope and limits
Personal API tokens (`act_…`, shown once, SHA-256 at rest) are capped at the lower of their chosen role and the owner's
current role, cannot create tokens, and stop when the owner is suspended or removed. The groups claim and SCIM 2.0
work in **OIDC mode only**; Matura mode keeps its own member sync and refuses them, and tokens work in every mode.
A SCIM member's role is the strongest role among their groups (default viewer). App connectors that need sign-in
connect per person with OAuth (RFC 9728/8414, PKCE, Client ID Metadata Documents with dynamic registration as the
fallback); a run acts as whoever started or resumed it, so a reviewer who resumes after an approval uses their own
connection. Not built: a hand-entered OAuth client id/secret (GitHub and Slack still need keys), OAuth connectors in
exported code (it fails with a clear message), and a validator diagnostic for "starter identity on a scheduled run"
(runtime error only). New setting `AGENTCANVAS_PUBLIC_URL`; unset, the first CORS origin is used (right for development).

### Files and images: references in state, bytes in the project
A file field is a channel typed `{"type":"object","format":"file"}` (optionally `contentMediaType`, e.g. `image/*`);
runs and checkpoints hold only a reference (`fil_…`, name, media type, size), never bytes. Uploads are capped
(10 MB per file, 1024 MB per project, configurable), checked by content, served attachment-only under a locked-down
CSP, and removed by the retention job. Only the LangGraph runtime shows files to a model; every other framework's
export lists a file field in GAPS.md as "carried as plain data". Not built: audio and video, OCR, file upload by
deployment-key callers (they send inline `data`), files for Planning Agents, and downloads by share visitors.

### Half-finished features finished; `GET /projects/{id}/models/resolve` removed
`GET model-defaults` already returns the resolved model for every purpose and the Settings page uses that, so the
unused `models/resolve` route was removed (it had been released: **a breaking API change**; restore it from
`d4ed815` if a client needs it). `settings.experimental` was dropped from the IR with IR 1.3 and a load-time migration
that strips it from 1.0-1.2 documents. Organisation overrides (Settings › Organisation) are bounded by the server:
local MCP can be switched off but never on when the server forbids it; the four rate limits can be lowered, never
raised; audit retention can be longer, never shorter; the operator's per-environment limit still wins. Node defaults
cover retry, timeout and cache only, the settings LangGraph applies. *Reverse:* delete an organisation's override to go
back to the server's value.

### Replicas: shared answer cache, budgets by database, routing by price
The answer cache and a remote agent's pending questions live in the database (tables `answer_cache`, `a2a_reply`, with
row-level security). Day and month budgets are read from the runs table after each model call, so two servers overshoot
by at most one model call per run; the in-process spend tally is gone. `cheapest_of` routing (opt-in on the model block)
picks the cheapest *ready* candidate by input plus output price at run start: local models are never routed (their
server may be down; use them as backups), and policies that apply only to a named environment are not enforced during
routing, as in the editor. A person in the spending report is the conversation's creator; runs from deployments,
schedules and events show as "no person". *Reverse:* drop `cheapest_of` from a block to use its own model.

### Claude Managed Agents and Git sync: built from the reference docs, not run against the services
The Managed Agents export (`ant apply` bundle: `agent.md`, `environment.yaml`, optional `vault.yaml`, skills) follows the
file shapes in the claude-api reference; the Git round trip uses the GitHub REST API through httpx. Neither has been run
against the real service (tests use golden files and a stateful mock transport), so a first real `ant apply` or pull
request may need a small fix. Settings with no Managed Agents counterpart (temperature, max tokens, add-ons, a
forced answer shape) are listed under "did not carry over" rather than guessed. Git sync's conflict rule: the draft is
replaced from the branch only when the draft and the branch have not both changed since the last sync.

### Knowledge and memory at scale: what was built, what was not
Knowledge search on Postgres uses pgvector and full-text search (1,000,000 chunks per base); SQLite keeps the in-process
engine (50,000). Memory's semantic index is a **server** setting (`AGENTCANVAS_MEMORY_EMBEDDING`), not per project: the
LangGraph store's vector column has one size per store. Re-crawl is an interval per knowledge base with its own job,
not a cron trigger (cron triggers belong to workflows). OCR for scanned PDFs was not added: no pure-Python engine is
installed and a real one is a heavy system or model dependency; the "needs OCR first" message stays. Reindexing memories
refreshes their `updated_at`, which restarts their retention clock (documented). *Reverse:* none needed.

### The Copilot's default engine stays the loop; AG-UI was not built
A review agent made Deep Agents the default engine and every Copilot suite passes with it, but the real-model
evaluation (`make copilot-evals` against Sonnet 5.5 and a local model) has never run, and the browser tests were not run
against it. That commit (`6423f66` on the agent's branch `feat-copilot`) was left out; `AGENTCANVAS_COPILOT_ENGINE`
still selects the engine. *Reverse:* cherry-pick it once the evaluation scores match. The AG-UI wire for the Copilot
(PLAN.md) was not built: it would run beside the SSE format the web reads, with no `ag-ui` dependency to build on
and no gain except CopilotKit interop, which the plan itself rejects.

### S-10 needed a test, not a change
The designed-agent header puts the block's name and the workflow's name into a docstring. The block's name is
restricted to an identifier by the IR, and the workflow's name goes through `json.dumps`, which escapes quotes, so
neither can close the docstring today. A test now sends a hostile workflow name through all nine generators and checks
the result has no code from it, so a later edit that breaks either guard fails.

### LiteLLM stays at 1.105.7, one step short of the newest
The framework pins were raised to the releases of late October (HARDENING 1.7), except LiteLLM: 1.106.x makes Google
ADK's tool calls fail with "No module named 'mcp'", a package it imports without declaring. 1.105.7 is the newest
that passes the ADK, parts, conformance and interop tests. *Reverse:* raise it in `designs.AGENT_FRAMEWORKS` (the pins
test lists every other place) once a LiteLLM release declares `mcp` or ADK stops importing it that way.
*Rechecked 2026-11-09:* the import is LiteLLM's own, not ADK's: `acompletion()` with tools loads its MCP proxy
handler, which imports `mcp.types` (a regression from LiteLLM PR #42568, issue BerriAI/litellm#46045). The fix
(PR #45961) is merged on LiteLLM's main branch but in no release up to 1.108.3, and ADK 2.14 itself declares
`litellm>=1.84,<1.106.3`. So 1.105.7 stays; the ADK smoke check now offers a tool, so it would catch the break.

### Merging the agents' work: cherry-picks onto the branch, not merges
The review's fixes were made in parallel by four agents in their own worktrees, each rebased onto this branch and
cherry-picked here, so the branch stays one line of commits, one concern each. Their changelog lines arrived in their
commit messages and were added in separate "Changelog:" commits.

### One decrypted secrets cache per API process stays (S-9)
The review asked to decrypt secrets per project with a short lifetime instead of keeping every organisation's values
in one process-wide dictionary. I did not: code that runs inside the API process can already read the KMS key and the
database URL, so splitting the cache would not stop it, and the real defences are elsewhere (workspace code runs in
the sandbox, which staging and production now require, S-8). The masking side of the item is done (encoded forms,
a 6-character minimum). *Reverse:* make `SecretCache` load on first use per project with a TTL.

### HARDENING.md rewritten as an open list; the "Done" table is gone
The deep review of 2026-10-29 (seven read-only passes over run-time, control plane, security, web, Copilot and
evaluation, adapters and exports, docs and completeness, plus a check of MCP 2026-07-28, A2A 1.0, the Anthropic and
OpenAI platforms and the competitors) produced about 90 findings and a plan. The old file was two thirds history.
The rewrite keeps only open work, with ids (`R-1`, `S-2`, …) so a PR can name its item, and marks each finding
*confirmed* (reproduced in this repository) or *plausible* (from reading). What was done lives in `CHANGELOG.md`
and this log, which git already keeps. *Reverse:* `git show 8304610:HARDENING.md` has the last version with the
Done table. **Nothing in the new list is fixed yet**; the review found them, the fixes are the next passes, in the
order the file's last section suggests (the confirmed highs and the one critical first).

### Reviewers' reproductions stay out of the repository
The scripts that confirmed findings (`scratchpad/test_double_cancel.py`, `n8n_loop.py`, `egr.py`, `blk.py`,
`repro1-5.py`, `wipe/store.wipe.test.ts`) are throwaway probes in the session's scratchpad, not tests: each fix
lands with its own regression test written against the fixed behaviour, as the standing rule says.

## 2026-10-28

### The RLS migration stays one transaction (the plan said split it)
Enabling row-level security and creating a policy change only the catalogue (no table is rewritten), so the 35
tables are locked for a moment, and the 60 s `lock_timeout` stops the migration rather than queueing traffic behind
it. Splitting it would mean committing inside a migration, which breaks the one-transaction advisory lock that keeps
replicas from migrating at once, and installs that upgraded since 2026-10-17 have already run it. *Reverse:* write a
new migration that drops and recreates the policies table by table, if a large install ever times out on it.

### The Copilot's "interrupts" are its proposals, not a checkpointer
The plan wanted LangGraph interrupts, with a persistent checkpointer per conversation, before real test runs and
workspace file writes. Both are approvals, and the product already has one: nothing the Copilot proposes changes
anything until the person accepts. Real runs already ask once per conversation (the permission card); files now
ride in the proposal and are written on accept, credited in the workspace's git history. This needs no second state
store beside the conversation row and works the same on both engines. *Reverse:* if the Copilot ever needs to
pause mid-turn for something that is not a proposal, add the checkpointer then.

### Only the Claude models get catalog prices; the rest are the operator's to set
Costs, USD budgets and cost alerts all read the catalog's prices, and it had none. I filled in the Claude models
from Anthropic's published list prices (as of 2026-10-06) and left the other providers empty rather than write down
prices I could not check; `AGENTCANVAS_MODEL_PRICES` lets an operator price them (or record negotiated prices),
and W015 still names any model a USD budget cannot count. *Reverse:* remove the `price_*` fields from `models.json`.

### Opening another project's workflow switches the app to that project
The Inbox spans the organisation (documented, and reviewers want one queue), but every page reads the project chosen
at start-up, so a workflow reached from it could open with the wrong project's blocks and models. I made the editor
follow the workflow (remember its project, load the page again) rather than narrow the Inbox to one project. A
once-per-page marker stops a reload loop when the project cannot be chosen (Matura workspaces, blocked storage).
Inbox items from another project show its name. *Reverse:* drop `followProject` in `editor/store.ts`.

### A workflow with no steps is refused at run time, not flagged as an error
An empty canvas shows no red (a new workflow is not "wrong"), but running it reached LangGraph and failed with a
stack-trace message. `/runs` now answers 422 "no steps yet" before anything is recorded. *Reverse:* remove the check
in `_runnable_workflow`.

### Preferences: the theme follows the person, Simple mode stays with the browser
The plan had both follow the person. Simple mode decides which blocks and panels the editor shows; syncing it made
every browser of the person flip when one did, and the browser tests (one shared dev user) depend on each other's
choice. The theme is cosmetic and safe to sync. The API keeps `simple_mode` for a client that wants it. *Reverse:*
apply `preferences.simple_mode` in `api/preferences.ts`.

### The IR's `settings.experimental` flag stays, unread
Nothing reads it. Removing it from the IR would make documents that contain it invalid (the IR refuses unknown
fields), for no gain; I010 already says when a block uses an upstream feature that may change, and the palette now
marks beta blocks. *Reverse:* remove it with an IR version bump and a migration of stored documents.

### The Copilot work so far, and the calls in it
- **Deep Agents is built, not yet the default.** `AGENTCANVAS_COPILOT_ENGINE=deepagents` runs it; every Copilot test
  runs on both engines and passes. I left the loop as the default because neither engine has been measured on a
  real model. Switch the default when the nightly evaluation (below) shows Deep Agents at least as good; reverse by
  leaving the variable unset.
- **The nightly evaluation needs a key from you**: add `ANTHROPIC_API_KEY` as a repository secret (Settings →
  Secrets → Actions). Until then the job says it was skipped. It uses `anthropic:claude-haiku-4-5` unless you set
  the repository variable `COPILOT_EVAL_MODEL` (147 cases per engine per night, more as templates are added). Step by step, with what it checks:
  `docs/agent-canvas-spec/build-guide/14-copilot-evaluation.md`.
- **AG-UI: the event names and shapes, not the `ag-ui-langgraph` bridge.** The bridge expects its own agent
  wrapper and input contract, which do not fit our sign-in, proposals and saved conversations; the panel reads our
  stream, which now carries `text_delta` and `todos` beside the events it had. A CopilotKit client could be added
  later by mapping these events one to one.
- **Markdown: a small renderer of our own (about 120 lines), not react-markdown.** The Copilot's answers need a
  safe subset (headings, lists, code, links); ours turns everything into React elements, so no HTML or
  `javascript:` link from a reply reaches the page, and it adds nothing to the bundle.
- **Partial accept is decided in the browser.** The editor applies the ticked commands through the normal edit
  path (so Undo works as before) and the server records which were kept; a change that needs another (a wire and
  its step) is ticked and unticked with it.
- **New chat archives instead of deleting.** Conversations are kept until deleted from History; the retention job
  does not touch them yet (they are small). Say if you want them to follow the organisation's retention.
- **A turn's lease is 15 minutes.** A server that dies mid-turn leaves the conversation busy for at most that long;
  a person can also start a New chat meanwhile.

### Egress: how it is enforced, and the one place it is not
The proxy is our own (about 250 lines of standard library) rather than Squid or Envoy: it has to check a list that
differs per call and per project, which those would need a plugin or a control plane for; ours checks an HMAC-signed
list carried in the proxy credentials, so it keeps no state and the code cannot widen its own list. It refuses names
that resolve to private addresses unless the list names the address itself, so a project cannot reach the database
or cloud metadata by listing a name that points there. Header injection (`inject`) stays LangSmith-only: doing it
ourselves means intercepting TLS, which I will not add without a reason. The in-process mode has no single place
where code starts, so it cannot refuse per call: there an egress list is a lint error (E084) in staging and
production on top of the existing "not isolated" start-up warning; the subprocess mode refuses at run time.
Production compose now requires `AGENTCANVAS_EGRESS_SECRET` (in the changelog under Upgrading). *Reverse:* set
`sandbox.egress.enabled=false` (Helm) or drop the service; code then has no network, as before.

### Approved by you: the Copilot on Deep Agents with AG-UI, and egress enforced or refused
You said yes to both (2026-10-28). The Copilot moves to `deepagents` behind the adapter boundary, built behind a
setting so the current loop stays the default until the new one passes the same scripted suite; then the old loop
goes. Sandbox egress: enforced through an allow-list proxy for the Docker and remote backends; an allow-list on the
in-process or subprocess backend is refused in staging and production and warned about in dev. Order of work stays
as in `PLAN.md` §7: shell and design system first, then Settings, then the Copilot, with the egress work slotted in
as a hardening item. *Reverse:* say so; each lands as its own commits.

### PLAN.md: the product plan, and two decisions I am not taking alone
You asked for a plan to make the product feel finished (settings, the Copilot, anything else). `PLAN.md` at the root
is it, from three code surveys and a look at the open-source options. Two choices in it change architecture, so I
will not start them until you say so: swapping the Copilot's hand-written loop for Deep Agents with AG-UI as the
event stream (§2.1; my recommendation, with the reasons and the rejected CopilotKit option in the table), and how
to enforce sandbox egress outside LangSmith (§6). The shell, design system and Settings tracks need no decision and
are what I will start on next, on this branch, under the standing instruction. *Reverse:* edit the plan; nothing is
built from it yet.

### Wrong-shaped answers: a notice, not a validator
When the API answers JSON of another shape than a page expects (a stale cached app against an upgraded server), the page
ends in the error boundary. I added a hint to the notice (reload; the page and server may differ) and did not add runtime
validation of every response: it would add a schema library to the bundle and a second place where the shapes are
written, for a situation that Reload fixes. HTML with a 200 (a misrouted `/api`) is different, common and cheap to
detect, so the client now refuses it with a message that names the likely cause. *Reverse:* none needed.

### Working on one branch, no new PRs
You asked for this on 2026-10-28: I keep committing to `claude/hardening-3` (fast-forwarded to `main` after #72) and push it,
and I open no pull request until you say so. Anything I fix is in the branch's commits and in this file.

### TOML strings get their own writer
`json.dumps` is not a TOML string (surrogate pairs for emoji). A tiny `agentcanvas/tomltext.py` writes basic strings
with only what TOML requires escaped; Unicode stays as it is. One helper for all packagers, not six local fixes.

### Kubernetes: schema-check the manifests, no kind cluster on this machine
GitHub CI already deploys an export to a kind cluster. Locally I did not add one: it needs privileged containers and a
~1 GB node image beside your running apps, which crosses the rule that your Docker stays untouched. Instead the rendered
Helm output (platform chart; each export's chart with autoscaling and ingress on) goes through kubeconform against the
1.31 schemas, which needs no cluster and catches what `helm lint` does not (I checked: `replicas: "many"` is rejected).
*Reverse:* delete `kubeconform_run` from `scripts/ci-local.sh` and the two piped steps in `ci.yml`.

### The hand-written web response types stay
Looked at the remaining `apiFetch<{ id: string }>`-style types: they are one-field reads (`{ id }`, `{ url }`,
`{ api_key }`). Replacing them with generated types would add imports and change nothing the type checker can catch,
because `apiFetch` does not tie a path to a type. `HARDENING.md` already says so; it stays an accepted state, not a TODO.

### Dependabot #73 (nginx-unprivileged 1.29 to 1.31): safe to merge; and a scan that found new advisories
Built the web image on 1.31 and ran the whole `images` stage: Trivy 0/0, every probe through nginx passes, including the
share-link framing check. Run on the same day, the scan also started failing for the API image: authlib 1.8.0 (a
critical signature-verification bypass, two high) and dnspython 2.8.0, both pulled in only by `fastmcp` (the `mcp`
extra). I raised just those two in `uv.lock` (1.9.1, 2.9.0) instead of ignoring them, and the MCP tests pass. *Why:* the
scan is a gate because advisories keep arriving, and it did its job. *Reverse:* none needed; a new advisory with no
fix goes in `infra/.trivyignore` with a reason and an expiry.

### Python is capped at 3.14 for the server and every project we write
Found while testing Dependabot #69: with Python 3.15 on the machine, `uv` picked it for an export and the install failed
(`google-re2`, via `cel-python`, has no 3.15 wheel and needs a C++ toolchain). `requires-python = ">=3.12"` had no
ceiling, so any customer with a newer Python hit the same. Every pyproject we write (and the server's own) now says
`<3.15`, the newest tested version, with a test that fails on an unbounded one. *Cost:* raising the ceiling is a
deliberate step (test, then edit the seven places the test lists). *Reverse:* remove `<3.15` and the test.

### A global banner for failures nobody handled
101 fire-and-forget promises (`void save()`) failed silently. `GlobalErrors` shows the existing error toast for an
unhandled rejection or error, ignoring aborts, a 401 (sign-in takes over) and the browser's harmless ResizeObserver
notice. Shown on the share page too. *Why:* a screen that does not change and says nothing is the worst failure.

### Local CI deploys two exports for real
`scripts/ci-local.sh export` now also deploys the support-triage and the mixed-framework exports with Docker Compose on
free ports (project names fixed by the exports themselves, distinct from yours) and runs their `make smoke`, as the
GitHub workflow did. The whole stage (about 150 projects) passes. Run it before releases, not on every push.

### Dependabot PRs #68, #69, #71: verified, not merged (merging is yours)
- **#71 (11 web dependency updates): safe to merge.** `pnpm install --frozen-lockfile`, types, oxlint, 125 unit tests, the
  build and all 73 Playwright journeys pass on the bumped lockfile.
- **#68 (uv image digest): safe to merge.** The API image builds with it and imports the app.
- **#69 (Python 3.15 for the API image): do not merge yet.** `uv sync --frozen` on 3.15 fails: `google-re2` (a dependency
  of `cel-python`, which the product needs for expressions) has no 3.15 wheel and does not compile in the slim image.
  Revisit when `google-re2` ships 3.15 wheels; I would close the PR until then (Dependabot reopens it).
  *How I checked:* the same way I checked 3.14 for #45 (image build, framework imports, suite), stopping at the first
  failure.

## 2026-10-27

### A flaky test was a real race in the model bridge, not flakiness
`test_one_template_runs_on_both_frameworks_in_one_experiment` failed about one run in three on Postgres. I did not retry
or mark it flaky: the failing call had base URL `http://127.0.0.1:0`. `model_bridge()` registered a new bridge before its
`start()` finished, so a second step starting at that moment got a bridge that was not listening. This affected every
framework that uses the bridge (OpenAI Agents, CrewAI, ADK, ...) whenever two steps began at once. Fixed with a `ready`
event; the regression test captures the address at the moment of arrival (a test that read it afterwards passed without
the fix, which I caught by running it against the old code).

### nginx `auth_request` could not carry the share token: fixed, and now tested end to end
Found by running the production stack for real (the `images` stage): `auth_request` takes a fixed URI and does not
expand variables, so nginx asked the API for the literal `/_share_frame/$share_token`, never reached it, and every share
page got an empty `frame-ancestors`. A link's allowed-sites list (v0.30.0) therefore never worked in production nginx.
The token now travels in the original request URI (`map $request_uri`). `ci-images.sh` creates a real link with an
allow-list through nginx and checks the page's CSP, an unknown link (`'self'` only) and an unlisted link (`*`).
*Why this way:* no change to the API contract, and the page stays closed (no framing) if the API is unreachable.

### Finished runs are forgotten by size, not only by count
`Runner` kept up to 500 finished runs with all their events; a few big outputs could fill memory. Now 256 MB of finished
runs' events (`keep_bytes`), checked when a run finishes as well as when one starts. Costs one `json.dumps` per event.
*Reverse:* pass a larger `keep_bytes`, or `sys.maxsize` to turn it off.

### Concurrency is tested, not assumed
`test_concurrency.py`: 25 runs at once, six racing starts on one thread (exactly one wins), two simultaneous decisions on
one approval (one applies, one is told). All three held on the first try; they run on Postgres in CI.

### Trivy findings in the API image: fix, do not accept
Fixed what had a fix: `uv lock` upgrades of certifi, filelock and pypdf; pip removed from the runtime image (its vendored
urllib3, certifi, msgpack and setuptools were the rest); the image scan now rebuilds the runtime stage so a cached apt
layer cannot hide a published fix. Nothing was added to `infra/.trivyignore`. *Why:* ignoring is for what cannot be fixed.
*Reverse:* remove the `pip uninstall` line in `infra/Dockerfile.api` if something needs pip at run time.

### The `images` CI stage runs in its own compose projects
`scripts/ci-images.sh` uses projects `ac-ci-prod` / `ac-ci-local`, free ports and `:ci` image tags. *Why:* the old stage
used the same project and ports as `make docker-up`, so running CI would have stopped your running app.

### Access control is tested over every route, not a list
`test_auth_every_route.py` (a stranger gets nothing from any route; only `/v1/auth/config` and the health probes are
public) and `test_roles_every_route.py` (a viewer or reviewer can do only what `ROLE_EXCEPTIONS` allows). Both found no
gap. *Why:* a new route that forgets its check should fail the day it is added.

## 2026-10-20 to 2026-10-26

### Keyboard access stays in (PR #64 re-applied what #65 reverted)
Main had #62 merged and then reverted by #65, with no reason given. The hardening branch contained #62 and built on it
(port menu focus), so merging kept it. *Why:* the work depends on it and the PR said so. *Reverse:* revert the keyboard
commits (`10f957d`, the port-menu parts) and drop `e2e/keyboard.spec.ts`.

### oxlint, not ESLint, for the web app
typescript-eslint does not support TypeScript 7 (the project's version). oxlint parses TypeScript itself. Only
correctness rules are on; the React Compiler rules (`set-state-in-effect`, `refs`, `immutability`) are off because about
25 effects set state by design and rewriting them is a refactor, not a fix. *Reverse:* edit `apps/web/.oxlintrc.json`.

### Three response types stayed hand-written
Policies, Setup and CodeHelp differ from the generated types in which fields are optional (the generated types come from
input schemas with defaults). Swapping them needed `?.` everywhere or API changes. *Why:* not worth the churn yet.

### Chat fix: copy the run input, and degrade the save instead of failing it
The engine converted the caller's message dicts to objects in place, so saving the run's recording failed and the thread
stayed "running". Fixed at the cause (a plain copy at start) and at the effect (`_finish_bare` records the status and
frees the thread even when the full save fails). *Why:* an optional detail must never block freeing a conversation.

### The last events of a run are published after the run is saved
`RUN_FINISHED`/`RUN_ERROR` are held until the thread is released. *Why:* a client that sends its next message as soon as
it reads the last event used to get a 409. Cost: the last event arrives a few milliseconds later.

### Migrations take a Postgres advisory lock and a 60 s lock timeout
Replicas starting together crashed (3 of 4). The second waits for the first; DDL gives up after 60 s instead of queueing
every other query behind it. *Reverse:* delete `_serialize` in `apps/api/migrations/env.py`.

### Unexpected errors answer as problem+json with the request id; database "data errors" are 422
A NUL character or an out-of-range number is the client's fault, not a 500. The SQL is never in the response.

### Python 3.14 for the API image (PR #45)
Verified before you merged: image built with every framework and Mastra, ten frameworks ready, the API suite green on
3.14. *Why I treated it as safe:* the check was done, not assumed.

### Pull requests I left alone
#36 (the identity plan, written by someone else) was yours to merge; #63 and #48 were closed (superseded; Node 26 cannot
work with corepack).

### How I push
`gh` is not installed and git's credential helper is missing here, so I used the token already in `~/.git-credentials`
through git's own credential command, only to push and to open or edit PRs. Nothing was printed or stored elsewhere.
The classifier blocks me from merging and from deleting branches; those stay with you.
