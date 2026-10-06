# Run AgentCanvas on your computer

This guide takes you from a fresh machine to a working AgentCanvas in your browser, then checks that it works.
It needs no API keys: the first run uses built-in demo models. Adding real models is step 7.

!!! tip "Verified"
    These steps were run on a clean checkout of `main` (v0.5.0) on 2026-10-06: `make setup`, `make dev-fake`,
    `make dev-web`, then the smoke test in step 6 (run, approve, finished output) and a publish + API call.

## At a glance

| What | Where |
|---|---|
| Web app | <http://localhost:5173> |
| API | <http://localhost:8000> (health: `/healthz`, interactive API docs: `/docs`) |
| Data | `apps/api/agentcanvas.db` (SQLite) and `apps/api/workspaces/` (one git repo per project) |
| Sign-in | none: in local mode every request is the built-in admin `dev@localhost` |

## 1. Install the prerequisites

| Tool | Version | Why | Check |
|---|---|---|---|
| Git | any recent | clone the repo; project workspaces are git repositories | `git --version` |
| [uv](https://docs.astral.sh/uv/) | 0.8 or newer | Python packages; it also downloads Python 3.12 if you don't have it | `uv --version` |
| Node.js | 22 | the web app | `node -v` |
| pnpm | 10 | web packages | `pnpm -v` |
| make | any | the shortcuts below (optional: every target is a plain command you can type) | `make -v` |
| Docker Desktop | recent | **optional**: only for Postgres (step 8) or the container setup (step 9) | `docker version` |

Install commands:

=== "macOS"

    ```bash
    brew install git node@22 make
    curl -LsSf https://astral.sh/uv/install.sh | sh
    corepack enable && corepack prepare pnpm@10 --activate
    ```

=== "Linux"

    ```bash
    # Node 22 from your package manager or https://nodejs.org, then:
    curl -LsSf https://astral.sh/uv/install.sh | sh
    corepack enable && corepack prepare pnpm@10 --activate
    ```

=== "Windows"

    Use **WSL 2** (Ubuntu) and follow the Linux steps inside it: the Makefile and scripts assume a Unix shell.
    If you prefer native PowerShell, install uv with
    `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, Node 22 from nodejs.org,
    then `corepack enable; corepack prepare pnpm@10 --activate`, and type the commands shown under each `make`
    target instead of `make`.

Open a new terminal after installing so the tools are on your `PATH`.

## 2. Get the code

```bash
git clone https://github.com/marunsct/AgentCanvas.git
cd AgentCanvas
```

## 3. Install dependencies

```bash
make setup
```

This runs `cd apps/api && uv sync` (Python packages into `apps/api/.venv`) and `cd apps/web && pnpm install`.
The first run downloads a few hundred MB and can take a few minutes.

## 4. Start the API (terminal 1)

```bash
make dev-fake
```

Same as `cd apps/api && uv run uvicorn agentcanvas.dev.fake_server:app --reload --port 8000`.

On first start the API creates the SQLite database, runs migrations and seeds a default organisation, project and
admin user. You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
... api.started  auth=dev database=sqlite edition=dev env=dev
INFO:     Application startup complete.
```

Check it from another terminal: `curl http://localhost:8000/healthz` answers `{"status":"ok"}`.

!!! note "Demo models"
    `dev-fake` answers every model call with a scripted demo model, so nothing leaves your machine and nothing
    costs money. Tools, scripts, approvals, memory and the database are all real.

## 5. Start the web app (terminal 2)

```bash
make dev-web
```

Same as `cd apps/web && pnpm dev`. Open <http://localhost:5173>. The web dev server forwards `/api/*` to the API on
port 8000, so no extra configuration is needed.

You land on **Home**: a *Start the guided build* button, a *New workflow* box, eight template cards and your
workflow list. The top bar shows `you · admin`.

![Home page](img/home.png)

## 6. Smoke test (two minutes)

1. On Home, click the **Support triage (demo tools)** card. The editor opens with five steps on the canvas.
2. In the right-hand panel, open the **Run** tab.
3. Replace the input with `{"ticket": "Where is my invoice?"}` and click **▶ Run**.
4. The steps light up in turn. *Reply drafter* stops with an **Approval needed** box for the `send_reply` tool.
5. Click **Approve**.
6. *Streamed output* shows `reply_drafter Done — the tool call finished (demo model).` and the Output shows the
   tool's answer `sent: demo`. Every step shows a green *done* badge.

![A finished run](img/editor-run.png)

If you got this far, the API, the web app, the database and durable runs all work. Optional extra checks:

- **Inbox:** run again, don't approve, and open **Inbox** in the top bar: the pending approval is there too.
- **Chat:** open the **Chat assistant** template, switch the Run tab to **Chat**, and send a message.
- **Publish and call it over HTTP:** see [Publish and integrate](publish.md#call-a-deployment).
- **Tests:** `make test` runs the backend and web test suites (several minutes).

## 7. Use real models

Stop the API (Ctrl+C in terminal 1) and start it with your provider key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # the templates use Claude models
make dev-api                             # same as dev-fake, but with real models
```

`make dev-api` runs `cd apps/api && uv run uvicorn agentcanvas.api.main:app --reload --port 8000`. Provider keys are
read from the environment of that shell (an `apps/api/.env` file only sets `AGENTCANVAS_*` and `DATABASE_URL`, not
provider keys). Alternatively store the key as a project secret on the **Workspace** page and reference it from the
model's config as `"api_key": {"$secret": "ANTHROPIC_API_KEY"}`.

Pick models in a workflow on its **Chat model** resources (the `fast_model` / `smart_model` boxes on the canvas).
The catalog offers Claude Opus 5.5, Claude Sonnet 5.5, Claude Haiku 4.5, GPT-5.5 and GPT-5 mini.

!!! warning "OpenAI models need one extra package"
    The OpenAI integration is not installed by `make setup`. To use `openai:` models, run
    `cd apps/api && uv pip install langchain-openai` and set `OPENAI_API_KEY`. (`uv sync` removes it again, so
    re-run the install after the next `make setup`.) Without it a run fails with
    *"Initializing ChatOpenAI requires the langchain-openai package"*. The *Support triage (demo tools)* example
    uses `openai:gpt-5-mini` for classification: switch that model to a Claude model if you only have an
    Anthropic key.

Optional: `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY=...` add an **Open trace** link to each run.

## 8. Optional: Postgres instead of SQLite

SQLite is enough for trying the product. To run against Postgres (what production uses, with pgvector for semantic
memory search):

```bash
docker compose -f infra/docker-compose.yml up -d postgres
DATABASE_URL=postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas make dev-fake   # or dev-api
```

Migrations run automatically at startup. `make infra` also starts Redis, which the API does not need today.

## 9. Optional: the production-style container setup

`infra/compose.prod.yml` runs Postgres, a one-shot migration, two API replicas and nginx serving the built web app.
It signs in through an identity provider by default; for a desktop trial, switch it to local mode.

```bash
docker build -f infra/Dockerfile.api -t agentcanvas-api apps/api
docker build -f infra/Dockerfile.web -t agentcanvas-web .
cat > infra/.env.prod <<'EOF'
POSTGRES_PASSWORD=change-me-local
AGENTCANVAS_SECRET_KEY=replace-with-64-or-more-random-characters-replace-with-64-or-more-random
AGENTCANVAS_ENV=dev
AGENTCANVAS_AUTH_MODE=dev
ANTHROPIC_API_KEY=
EOF
docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait
```

Open <http://localhost:8080> (`infra/.env.prod` is git-ignored, so your keys stay out of commits). Stop with `docker compose -f infra/compose.prod.yml down` (add `-v` to delete the
data). Local mode (`AGENTCANVAS_AUTH_MODE=dev`) is refused when `AGENTCANVAS_ENV` is `staging` or `prod`; for a
real deployment follow [Deploy and operate](deploy.md).

!!! note
    This path follows `infra/compose.prod.yml` and the startup checks in the code, but was not run end to end
    for this guide (the image build needs Docker Hub access).

## 10. Optional: try sign-in, roles and invitations

Local mode makes you an admin with no sign-in. To try real sign-in with
[Keycloak](https://www.keycloak.org) on your machine:

1. `docker compose -f infra/docker-compose.yml --profile auth up -d keycloak` and open <http://localhost:8080>
   (admin / admin).
2. Create a realm (e.g. `agentcanvas`) and a user with an email address, marked *Email verified*.
3. Create a client `agentcanvas-web`: OpenID Connect, *Client authentication* off (public), *Standard flow* on,
   valid redirect URI `http://localhost:5173/auth/callback`, web origin `http://localhost:5173`.
4. Make the access token carry the audience `agentcanvas-api`: on the client's dedicated scope add an
   *Audience* mapper with included custom audience `agentcanvas-api`.
5. Start the API with:

    ```bash
    AGENTCANVAS_AUTH_MODE=oidc \
    AGENTCANVAS_OIDC_ISSUER=http://localhost:8080/realms/agentcanvas \
    AGENTCANVAS_OIDC_AUDIENCE=agentcanvas-api \
    AGENTCANVAS_OIDC_CLIENT_ID=agentcanvas-web \
    AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS='["you@example.com"]' \
    make dev-fake
    ```

The web app now redirects to Keycloak; the bootstrap email becomes an admin on first sign-in and can invite others
from **Members**. (Keycloak and the container setup both use port 8080: run one at a time.)

## Stop, reset and update

- **Stop:** Ctrl+C in both terminals.
- **Reset to a clean slate:** stop the API, then `rm apps/api/agentcanvas.db && rm -rf apps/api/workspaces`.
- **Update:** `git pull && make setup`, then restart both servers. Migrations run at startup.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make: command not found` (Windows) | Use WSL 2, or type the command each `make` target runs (shown above). |
| `uv: command not found` after installing | Open a new terminal, or add `~/.local/bin` to `PATH`. |
| `pnpm: command not found` | `corepack enable && corepack prepare pnpm@10 --activate`, or `npm i -g pnpm@10`. |
| `address already in use` on 8000 or 5173 | Another process holds the port: stop it (`lsof -i :8000` on macOS/Linux) or stop an old server. |
| The web app shows an error on every page | The API is not running or not on port 8000. Check `curl localhost:8000/healthz`. |
| A run fails with *requires the langchain-openai package* | See the warning in step 7. |
| A run fails with an authentication error from the provider | The key is missing or wrong: export it in the shell that starts `make dev-api`, or use a project secret (step 7). |
| `git` errors when the API starts or saves a workspace file | Install Git and make sure it is on `PATH`. |
| Startup says *AGENTCANVAS_AUTH_MODE=dev gives every request admin rights* | You set `AGENTCANVAS_ENV` to `staging`/`prod`; use `dev` locally or configure OIDC. |
| Startup says *oidc needs AGENTCANVAS_OIDC_ISSUER and AGENTCANVAS_OIDC_AUDIENCE* | Set both, or leave `AGENTCANVAS_AUTH_MODE` unset for local mode. |
| Something is odd after many experiments | Reset (above) and restart. |

More developer detail is in the [build guide](../agent-canvas-spec/build-guide/01-environment-setup.md) and its
[troubleshooting FAQ](../agent-canvas-spec/build-guide/11-troubleshooting-faq.md).
