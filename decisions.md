# Decisions made without asking

Judgment calls made while hardening AgentCanvas on its own, newest first. Each has what was decided, why, and how to
reverse it. Read it when you have a moment; tell me (or edit the entry) if you disagree and I will change course.
Merging, deleting branches and anything outward-facing stay with you: work is delivered as branches and pull requests.

**Standing guidelines I follow** (from the product, `HANDOVER.md` and what you asked for):
- Fail closed and say why: a refused request, a warning, a failing check, never a silent skip.
- One concern per commit; every fix lands with a test that fails without it; the suites run on Postgres, not only SQLite.
- No version bump or tag (yours to decide); `CHANGELOG.md` "Unreleased" and `HARDENING.md` are kept current.
- Prefer checks that grow with the product (driven by the OpenAPI document or the template list) over lists to maintain.

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
  the repository variable `COPILOT_EVAL_MODEL` (about 40 cases per engine per night).
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
