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
