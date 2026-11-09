# AgentCanvas monorepo tasks. Run `make help`.
.PHONY: help setup crewai framework mastra infra dev-api dev-fake dev-web docker-up docker-down docker-logs ci ci-full hooks test test-api test-web e2e copilot-evals screenshots framework-versions framework-bump lint fmt schema check docs

help:       ## show targets
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

setup:      ## install Python and web dependencies
	cd apps/api && uv sync
	cd apps/web && pnpm install

crewai:     ## CrewAI flows (S27): CrewAI in its own Python environment, apps/api/.frameworks/crewai, from its lock as in the image
	cd apps/api && uv venv --allow-existing --python 3.13 .frameworks/crewai \
	  && uv pip sync --require-hashes --python .frameworks/crewai/bin/python framework-locks/crewai.txt

framework:  ## mixed flows (S28), ADK (S29), Pydantic AI and Agno (S32), Agent Framework (S33), Strands and LlamaIndex (S35) workflows: a framework in its own environment, e.g. make framework FW=agno
	cd apps/api && uv venv --allow-existing --python $(if $(filter crewai,$(FW)),3.13,3.14) .frameworks/$(FW) \
	  && uv pip sync --require-hashes --python .frameworks/$(FW)/bin/python framework-locks/$(FW).txt

mastra:     ## Mastra workflows (S36): Mastra and its flow runtime from npm, as pinned by package-lock.json, in apps/api/.frameworks/mastra (needs Node.js 22.18+)
	cd apps/api && mkdir -p .frameworks/mastra && cp agentcanvas/adapters/mastra/package.json agentcanvas/adapters/mastra/package-lock.json .frameworks/mastra/ \
	  && npm ci --prefix .frameworks/mastra --no-audit --no-fund --loglevel=error

infra:      ## start local Postgres (Docker; ports on 127.0.0.1 only)
	docker compose -f infra/docker-compose.yml up -d postgres

dev-api:    ## API with real models (needs provider keys)
	cd apps/api && uv run uvicorn agentcanvas.api.main:app --reload --port 8000

dev-fake:   ## API with scripted fake models (no keys)
	cd apps/api && uv run uvicorn agentcanvas.dev.fake_server:app --reload --port 8000

dev-web:    ## web app on http://localhost:5173
	cd apps/web && pnpm dev

comma := ,
docker-up:  ## the whole app in Docker on http://localhost:7777 (builds the images) with every framework; FRAMEWORKS=none (or ids) for fewer
	FRAMEWORKS="$(or $(FRAMEWORKS),all)" WITH_MASTRA=$(if $(filter all mastra,$(subst $(comma), ,$(or $(FRAMEWORKS),all))),true,false) \
	  docker compose -f infra/compose.local.yml $(if $(wildcard infra/.env.local),--env-file infra/.env.local) up -d --build --wait
	@echo "AgentCanvas is running on http://localhost:$${WEB_PORT:-7777}"

docker-down: ## stop the Docker app (your data stays; add -v to the compose command to delete it)
	docker compose -f infra/compose.local.yml down

docker-logs: ## follow the Docker app's logs
	docker compose -f infra/compose.local.yml logs -f

test: test-api test-web  ## all unit/integration tests

test-api:
	cd apps/api && uv run pytest -q

test-web:
	cd apps/web && pnpm test

e2e:        ## Playwright end-to-end (starts servers itself)
	cd apps/web && pnpm e2e

MODEL ?= ollama:qwen3
ENGINE ?= loop
OUT ?= $(CURDIR)/copilot-evals.json
copilot-evals: ## the Copilot evaluation, both suites, on this computer: MODEL=ollama:qwen3 (or provider:model; the offline demo Copilot when it cannot be reached) ENGINE=loop|deepagents OUT=report.json
	cd apps/api && uv run python -m agentcanvas.copilot.evals --model "$(MODEL)" --engine "$(ENGINE)" --suite both --out "$(OUT)" --fallback-to-demo

framework-versions: ## which agent framework pins are behind their latest release, with each one's changelog
	cd apps/api && uv run python scripts/framework_versions.py

framework-bump: ## move one framework pin everywhere it is written: make framework-bump PKG=crewai VERSION=1.15.39
	cd apps/api && uv run python scripts/framework_versions.py bump "$(PKG)" "$(VERSION)"
	cd apps/api && uv run python scripts/framework_versions.py lock
	cd apps/api && uv run pytest -q -p no:cacheprovider tests/test_framework_pins.py tests/test_framework_versions.py

screenshots: ## retake the pictures in docs/user-guide/img (fake-model server, made-up data)
	d=$$(mktemp -d) && cd apps/web && DATABASE_URL="sqlite+aiosqlite:///$$d/shots.db" AGENTCANVAS_WORKSPACES_DIR="$$d/ws" \
	  SCREENSHOTS=1 E2E_PROD=1 npx playwright test e2e/screenshots.spec.ts; rc=$$?; rm -rf "$$d"; exit $$rc

lint:       ## ruff, pyright, import contracts, tsc, oxlint
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
	cd apps/web && pnpm typecheck && pnpm lint

fmt:        ## format Python
	cd apps/api && uv run ruff format . && uv run ruff check --fix .

schema:     ## export IR JSON Schema + node manifests to packages/ir-schema
	cd apps/api && uv run python scripts/export_ir_schema.py

migrate:    ## apply database migrations (DATABASE_URL)
	cd apps/api && uv run alembic upgrade head

check:      ## every CI gate except e2e and the docs build (lint, formats, types, contracts, schema/type drift, tests, build)
	$(MAKE) lint
	$(MAKE) schema
	cd apps/web && pnpm gen
	git diff --exit-code -- packages/ir-schema apps/web/src/api/generated.ts apps/web/src/ir/generated.ts
	$(MAKE) test
	cd apps/web && pnpm build

ci:         ## local CI, the default gates: check, strict docs build, dependency audit, Helm lint (see scripts/ci-local.sh)
	scripts/ci-local.sh default

ci-full:    ## local CI, everything: framework environments, the full API suite, images and compose, e2e (slow)
	scripts/ci-local.sh full

hooks:      ## run lint, types and web tests before every git push (skip once with --no-verify)
	git config core.hooksPath .githooks
	@echo "pre-push hook installed"

docs:       ## documentation site on http://localhost:8001 (MkDocs Material)
	uvx --from mkdocs==1.6.1 --with mkdocs-material==9.8.1 mkdocs serve -a localhost:8001
