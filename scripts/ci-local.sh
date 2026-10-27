#!/usr/bin/env bash
# Local CI: the same gates as .github/workflows/ci.yml, on this machine. `make ci` (default stages) or
# `scripts/ci-local.sh <stage>...`. Stages: fast api-envs api mastra-types docs audit helm frameworks export images e2e.
# Group names: default, full. The `api` stage runs the suite on Postgres (a throwaway container) when Docker is there,
# as the GitHub workflow did; without Docker it falls back to SQLite and says so.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
FRAMEWORKS="google_adk pydantic_ai agno microsoft_agent_framework strands llamaindex"
declare -a OK=() BAD=() SKIP=()

need() { command -v "$1" >/dev/null 2>&1; }
HELM_IMAGE=alpine/helm:3.17.3
have_helm() { need helm || need docker; }
helm_run() {   # helm, from the machine or (no install needed) from its official image
  if need helm; then helm "$@"; else
    docker run --rm -v "$ROOT:$ROOT" -w "$PWD" "$HELM_IMAGE" "$@"   # only the repository is shared with Docker
  fi
}
stage() {  # stage <name> <command...>: run, record, keep going so one report shows every failure
  local name=$1; shift
  printf '\n\033[1m== %s ==\033[0m\n' "$name"
  if ( set -e; "$@" ); then OK+=("$name"); else BAD+=("$name"); fi
}
skip() { printf '\n== %s == skipped: %s\n' "$1" "$2"; SKIP+=("$1 ($2)"); }

s_fast() {   # lint, schema/type drift, web typecheck/tests/build: what `make check` already does
  make -C "$ROOT" check
}
s_api_envs() {   # every framework in its own environment, as the conformance kit and the export tests need
  make -C "$ROOT" crewai
  for fw in $FRAMEWORKS; do make -C "$ROOT" framework FW="$fw"; done
  if need node; then make -C "$ROOT" mastra; fi
}
PG_NAME=ac-ci-pg
pg_stop() { docker rm -f "$PG_NAME" >/dev/null 2>&1 || true; }
pg_start() {   # a throwaway pgvector Postgres on a free port; prints its URL
  pg_stop
  local port; port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
  docker run -d --name "$PG_NAME" -e POSTGRES_USER=agentcanvas -e POSTGRES_PASSWORD=agentcanvas -e POSTGRES_DB=agentcanvas \
    -p "127.0.0.1:$port:5432" pgvector/pgvector:pg16 >/dev/null || return 1
  for _ in $(seq 1 30); do
    if docker exec "$PG_NAME" pg_isready -U agentcanvas >/dev/null 2>&1; then
      echo "postgresql://agentcanvas:agentcanvas@127.0.0.1:$port/agentcanvas"; return 0
    fi
    sleep 1
  done
  return 1
}
s_api() {
  local url=""
  if need docker && url=$(pg_start); then
    echo "API tests on Postgres ($url)"; export TEST_DATABASE_URL=$url AGENTCANVAS_ENV=test
    trap pg_stop RETURN
  else
    echo "WARNING: no Docker, or Postgres did not start: the API tests run on SQLite only (row-level security and the Postgres dialect are not tested)"
  fi
  ( cd apps/api && uv sync --frozen && uv run python scripts/export_ir_schema.py \
      && git diff --exit-code ../../packages/ir-schema agentcanvas/copilot/knowledge/user-guide \
      && uv run python scripts/smoke_mcp_gallery.py && uv run python scripts/check_part_envs.py && uv run pytest -q )
}
s_frameworks() {   # real agents from each framework, wrapped and drawn on the canvas, against fake models (ci.yml `frameworks`)
  local spec check with
  for spec in \
    "openai_agents|openai-agents==0.23.1" "crewai|crewai==1.15.25" "google_adk|google-adk==2.11.0 litellm==1.104.2" \
    "pydantic_ai|pydantic-ai-slim[openai]==2.54.0" "agno|agno==3.1.1 openai==2.54.0" "strands|strands-agents[openai]==1.58.1" \
    "llamaindex|llama-index-workflows==2.25.0 llama-index-core==0.14.25 llama-index-llms-openai-like==0.8.1" \
    "microsoft_agent_framework|agent-framework-core==1.20.0 agent-framework-openai==1.16.0" \
    "claude_agent_sdk|claude-agent-sdk==0.2.164" "agentspec|pyagentspec==26.3.1" \
    "bridge_langgraph|pyagentspec[langgraph]==26.3.1 jinja2" "bridge_wayflow|wayflowcore==26.3.1 jinja2" \
    "bridge_openai_agents|pyagentspec==26.3.1 openai-agents==0.23.1"; do
    check=${spec%%|*}; with=${spec#*|}
    ( cd apps/api && uv sync --frozen -q && uv run $(printf -- '--with %s ' $with) python scripts/smoke_frameworks.py "$check" ) \
      || { echo "frameworks: $check failed"; return 1; }
  done
}
s_export() {   # every template exports to every target; the deploy files lint; each export passes its own tests (ci.yml `export`)
  local ex d c t rc
  mkdir -p "$ROOT/.ci-tmp"; ex=$(mktemp -d -p "$ROOT/.ci-tmp"); trap 'rm -rf "$ex"' RETURN   # inside the repo: Docker shares it
  ( cd apps/api && uv sync --frozen -q && uv run python scripts/export_templates.py "$ex" ) || return 1
  for t in "$ex"/*/*/*/deploy/*/deploy.sh; do bash -n "$t" || return 1; done
  if have_helm; then
    for c in "$ex"/kubernetes/*/*/deploy/kubernetes/chart; do
      helm_run lint "$c" --set image.repository=example/app >/dev/null \
        && helm_run template x "$c" --set image.repository=example/app --set ingress.enabled=true --set ingress.host=a.example.com >/dev/null \
        || return 1
    done
  else echo "export: neither helm nor docker is here, the Kubernetes charts are not linted"; fi
  if need docker; then
    for d in "$ex"/local/*/* "$ex"/server/*/*; do
      touch "$d/.env"
      DOMAIN=a.example.com POSTGRES_PASSWORD=x docker compose -f "$d"/deploy/*/docker-compose.yml config -q || { rm "$d/.env"; return 1; }
      rm "$d/.env"
    done
  else echo "export: docker missing, the compose files are not checked"; fi
  for t in "$ex"/aws/*/*/deploy/aws/task-definition.json; do python3 -m json.tool "$t" >/dev/null || return 1; done
  # ~150 projects, each with its own environment: one at a time, and each environment is deleted once its tests ran
  # (the cloud runners had a disk for this; a laptop fills up)
  for d in "$ex"/local/*/*; do
    ( cd "$d" && make -s test ); rc=$?; rm -rf "$d/.venv" "$d"/*/.venv
    [ $rc -eq 0 ] || { echo "export tests failed in $d"; return 1; }
  done
  for d in "$ex"/openai_agents/*/* "$ex"/crewai/*/* "$ex"/google_adk/*/* "$ex"/pydantic_ai/*/* "$ex"/agno/*/* \
           "$ex"/microsoft_agent_framework/*/* "$ex"/strands/*/* "$ex"/llamaindex/*/*; do
    ( cd "$d" && uv sync -q && uv run pytest -q ); rc=$?; rm -rf "$d/.venv"
    [ $rc -eq 0 ] || { echo "export tests failed in $d"; return 1; }
  done
  if need node; then
    for d in "$ex"/mastra/*/*; do
      ( cd "$d" && npm install -s --no-audit --no-fund && npm test ); rc=$?; rm -rf "$d/node_modules"
      [ $rc -eq 0 ] || { echo "export tests failed in $d"; return 1; }
    done
  fi
}
s_mastra_types() {   # the Mastra runtime is TypeScript that Node runs with its types stripped: check them with tsc, strictly
  local d tsc="$ROOT/apps/web/node_modules/.bin/tsc" src="$ROOT/apps/api/agentcanvas/adapters/mastra"
  [ -x "$tsc" ] || { echo "mastra-types: run pnpm install in apps/web first (tsc lives there)"; return 1; }
  mkdir -p "$ROOT/.ci-tmp"; d=$(mktemp -d -p "$ROOT/.ci-tmp"); trap 'rm -rf "$d"' RETURN
  cp "$src"/package.json "$src"/package-lock.json "$src"/runtime.ts "$src"/flow.ts "$src"/worker.ts "$d"/
  cat > "$d/tsconfig.json" <<'JSON'
{ "compilerOptions": { "target": "ES2023", "module": "NodeNext", "moduleResolution": "NodeNext", "strict": true,
    "noEmit": true, "skipLibCheck": true, "allowImportingTsExtensions": true, "types": ["node"] },
  "files": ["runtime.ts", "flow.ts", "worker.ts"] }
JSON
  ( cd "$d" && npm ci --no-audit --no-fund --loglevel=error && npm i --no-save --no-audit --no-fund --loglevel=error @types/node@22 @types/nunjucks \
      && "$tsc" -p . )
}
s_docs() { uvx --with mkdocs-material mkdocs build --strict -d "$(mktemp -d)"; }
s_audit() {
  ( cd apps/api && uv export --frozen --no-dev --no-emit-project --no-hashes -o "${TMPDIR:-/tmp}/ac-req.txt" \
      && uvx pip-audit==2.10.1 -r "${TMPDIR:-/tmp}/ac-req.txt" --disable-pip --no-deps )
  ( cd apps/web && pnpm audit --prod --audit-level high )
}
s_helm() {
  helm_run lint infra/helm/agentcanvas -f infra/helm/agentcanvas/ci/test-values.yaml --strict
  helm_run template agentcanvas infra/helm/agentcanvas -f infra/helm/agentcanvas/ci/test-values.yaml >/dev/null
}
s_images() {   # build and scan both images, probe the production and local compose stacks (own projects and ports)
  scripts/ci-images.sh
}
s_e2e() { make -C "$ROOT" e2e; }

run() {
  case $1 in
    fast) stage fast s_fast ;;
    api-envs) stage api-envs s_api_envs ;;
    api) stage api s_api ;;
    mastra-types) need node && stage mastra-types s_mastra_types || skip mastra-types "node missing" ;;
    docs) need uvx && stage docs s_docs || skip docs "uvx missing" ;;
    audit) need uvx && stage audit s_audit || skip audit "uvx missing" ;;
    helm) have_helm && stage helm s_helm || skip helm "helm and docker missing" ;;
    frameworks) stage frameworks s_frameworks ;;
    export) stage export s_export ;;
    images) need docker && stage images s_images || skip images "docker missing" ;;
    e2e) stage e2e s_e2e ;;
    default) for s in fast docs audit helm; do run "$s"; done ;;
    full) for s in api-envs api mastra-types fast docs audit helm frameworks export images e2e; do run "$s"; done ;;
    *) echo "unknown stage: $1" >&2; exit 2 ;;
  esac
}

for s in "${@:-default}"; do run "$s"; done
printf '\n\033[1mLocal CI\033[0m  passed: %s\n' "${OK[*]:-none}"
[ ${#SKIP[@]} -gt 0 ] && printf 'skipped: %s\n' "${SKIP[*]}"
if [ ${#BAD[@]} -gt 0 ]; then printf '\033[31mFAILED: %s\033[0m\n' "${BAD[*]}"; exit 1; fi
