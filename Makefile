# AgentCanvas monorepo tasks. Run `make help`.
.PHONY: help setup infra dev-api dev-fake dev-web test test-api test-web e2e lint fmt schema check docs

help:       ## show targets
	@grep -E '^[a-z-]+:.*## ' Makefile | sed 's/:.*## /\t/'

setup:      ## install Python and web dependencies
	cd apps/api && uv sync
	cd apps/web && pnpm install

infra:      ## start local Postgres + Redis (Docker)
	docker compose -f infra/docker-compose.yml up -d postgres redis

dev-api:    ## API with real models (needs provider keys)
	cd apps/api && uv run uvicorn agentcanvas.api.main:app --reload --port 8000

dev-fake:   ## API with scripted fake models (no keys)
	cd apps/api && uv run uvicorn agentcanvas.dev.fake_server:app --reload --port 8000

dev-web:    ## web app on http://localhost:5173
	cd apps/web && pnpm dev

test: test-api test-web  ## all unit/integration tests

test-api:
	cd apps/api && uv run pytest -q

test-web:
	cd apps/web && pnpm test

e2e:        ## Playwright end-to-end (starts servers itself)
	cd apps/web && pnpm e2e

lint:       ## ruff, pyright, tsc
	cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
	cd apps/web && pnpm typecheck

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

docs:       ## documentation site on http://localhost:8001 (MkDocs Material)
	uvx --with mkdocs-material mkdocs serve -a localhost:8001
