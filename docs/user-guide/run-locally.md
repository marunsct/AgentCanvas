# Run AgentCanvas on your computer

This guide takes you from a fresh machine to a working AgentCanvas in your browser, then checks that it works.
There are two ways to run it:

- **Docker (quickest):** one command runs the whole app; a first-run screen asks which model to use. Only Docker
  is needed. See [Run everything in Docker](#run-everything-in-docker).
- **From source (for development):** the API and the web app run as dev servers with live reload. It needs no
  API keys at first: it uses built-in demo models. Steps 1 to 10 below.

## Run everything in Docker

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows, macOS) or Docker Engine with
the Compose plugin (Linux), and Git.

```bash
git clone https://github.com/marunsct/AgentCanvas.git
cd AgentCanvas
make docker-up        # or: docker compose -f infra/compose.local.yml up -d --build --wait
```

The first build takes a few minutes. Then open <http://localhost:8080>: Home shows **Set up your model**.

1. Click **Choose a model**.
2. Pick a **provider**: hosted (Anthropic, OpenAI, Azure OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, or
   any OpenAI-compatible API such as OpenRouter or Together) or **on your machine** (Ollama, LM Studio, vLLM,
   llama.cpp).
3. Pick or type the **model** (suggestions are listed for each provider).
4. Hosted providers: paste your **API key**. It is stored encrypted as a project secret and never shown again.
   Local servers: leave **Server address** empty to use the default port on your computer.
5. Click **Test connection**: AgentCanvas sends one short message and shows the reply, or the error.
6. Click **Save**. Templates you open from now on use this model. You can change it any time from **Model** in the
   top bar, and pick a different model on any Chat model box on the canvas.

Models on your own computer are reached from inside Docker through `host.docker.internal`, so start Ollama
(`ollama serve`, then `ollama pull llama3.3`) or LM Studio's local server before testing.

| Command | Does |
|---|---|
| `make docker-up` | build and start (again) |
| `make docker-logs` | follow the logs |
| `make docker-down` | stop; your workflows and keys stay in Docker volumes |
| `docker compose -f infra/compose.local.yml down -v` | stop and delete all data |

Optional settings (another port, server-wide provider keys) go in `infra/.env.local`: copy
`infra/.env.local.example` and fill it in; `make docker-up` picks it up. The Docker setup has no sign-in (you are
the local admin): don't expose it to other people. For a shared server follow [Deploy and operate](deploy.md).

## From source

!!! tip "Verified"
    The from-source steps were run on a clean checkout on 2026-10-06: `make setup`, `make dev-fake`, `make dev-web`,
    the smoke test in step 6, a publish + API call, and the first-run model screen. The Docker stack is started and
    checked (first-run setup included) by CI on every change.

### At a glance

| What | Where |
|---|---|
| Web app | <http://localhost:5173> |
| API | <http://localhost:8000> (health: `/healthz`, interactive API docs: `/docs`) |
| Data | `apps/api/agentcanvas.db` (SQLite) and `apps/api/workspaces/` (one git repo per project) |
| Sign-in | none: in local mode every request is the built-in admin `dev@localhost` |

### 1. Install the prerequisites

| Tool | Version | Why | Check |
|---|---|---|---|
| Git | any recent | clone the repo; project workspaces are git repositories | `git --version` |
| [uv](https://docs.astral.sh/uv/) | 0.8 or newer | Python packages; it also downloads Python 3.12 if you don't have it | `uv --version` |
| Node.js | 22 | the web app | `node -v` |
| pnpm | 10 | web packages | `pnpm -v` |
| make | any | the shortcuts below (optional: every target is a plain command you can type) | `make -v` |
| Docker Desktop | recent | **optional**: only for Postgres (step 8) | `docker version` |

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

### 2. Get the code

```bash
git clone https://github.com/marunsct/AgentCanvas.git
cd AgentCanvas
```

### 3. Install dependencies

```bash
make setup
```

This runs `cd apps/api && uv sync` (Python packages into `apps/api/.venv`) and `cd apps/web && pnpm install`.
The first run downloads a few hundred MB and can take a few minutes.

### 4. Start the API (terminal 1)

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

### 5. Start the web app (terminal 2)

```bash
make dev-web
```

Same as `cd apps/web && pnpm dev`. Open <http://localhost:5173>. The web dev server forwards `/api/*` to the API on
port 8000, so no extra configuration is needed.

You land on **Home**: a *Start the guided build* button, a *New workflow* box, eight template cards and your
workflow list. The top bar shows `you · admin`.

![Home page](img/home.png)

### 6. Smoke test (two minutes)

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

### 7. Use real models

Stop the API (Ctrl+C in terminal 1) and start it with real models:

```bash
make dev-api      # same as dev-fake, but calls the models you choose
```

`make dev-api` runs `cd apps/api && uv run uvicorn agentcanvas.api.main:app --reload --port 8000`. Then, in the web
app, click **Model** in the top bar (or **Choose a model** on Home) and pick any provider, hosted or local, with its
API key: the same first-run screen as in [Docker](#run-everything-in-docker). Every supported provider's integration
is installed by `make setup`.

You can also give keys to the whole server instead: export `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY` or `XAI_API_KEY` in the shell that starts
`make dev-api`. See [Models and API keys](models.md) for every provider and how keys are chosen.

Optional: `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY=...` add an **Open trace** link to each run.

### 8. Optional: Postgres instead of SQLite

SQLite is enough for trying the product. To run against Postgres (what production uses, with pgvector for semantic
memory search):

```bash
docker compose -f infra/docker-compose.yml up -d postgres
DATABASE_URL=postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas make dev-fake   # or dev-api
```

Migrations run automatically at startup. `make infra` also starts Redis, which the API does not need today.

### 9. Optional: the production-style container setup

To run what production runs (two API replicas behind nginx, sign-in through an identity provider), follow
[Deploy and operate](deploy.md#option-1-docker-compose-on-one-host). For trying AgentCanvas on one computer,
[Run everything in Docker](#run-everything-in-docker) is simpler.

### 10. Optional: try sign-in, roles and invitations

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
| A run fails with an authentication error from the provider | The key is missing or wrong: enter it again under **Model**, or check the project secret named after the provider on the **Workspace** page. |
| **Test connection** to Ollama / LM Studio fails with a connection error | The local server is not running, or listens on another port: start it, or enter its address under *Server address*. In Docker, the address must use `host.docker.internal`, not `localhost`. |
| A model shows *integration is not installed on this server* | Run `make setup` again (it installs every provider), or `docker compose … up --build`. |
| `make docker-up` fails with *port is already allocated* | Something else uses 8080: set `WEB_PORT=8090` in `infra/.env.local`. |
| `git` errors when the API starts or saves a workspace file | Install Git and make sure it is on `PATH`. |
| Startup says *AGENTCANVAS_AUTH_MODE=dev gives every request admin rights* | You set `AGENTCANVAS_ENV` to `staging`/`prod`; use `dev` locally or configure OIDC. |
| Startup says *oidc needs AGENTCANVAS_OIDC_ISSUER and AGENTCANVAS_OIDC_AUDIENCE* | Set both, or leave `AGENTCANVAS_AUTH_MODE` unset for local mode. |
| Something is odd after many experiments | Reset (above) and restart. |

More developer detail is in the [build guide](../agent-canvas-spec/build-guide/01-environment-setup.md) and its
[troubleshooting FAQ](../agent-canvas-spec/build-guide/11-troubleshooting-faq.md).
