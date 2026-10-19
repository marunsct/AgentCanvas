#!/usr/bin/env bash
# Local CI: the same gates as .github/workflows/ci.yml, on this machine. `make ci` (default stages) or
# `scripts/ci-local.sh <stage>...`. Stages: fast api-envs api docs audit web helm images e2e. Group names: default, full.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
FRAMEWORKS="google_adk pydantic_ai agno microsoft_agent_framework strands llamaindex"
declare -a OK=() BAD=() SKIP=()

need() { command -v "$1" >/dev/null 2>&1; }
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
s_api() {
  ( cd apps/api && uv sync --frozen && uv run python scripts/export_ir_schema.py \
      && git diff --exit-code ../../packages/ir-schema agentcanvas/copilot/knowledge/user-guide \
      && uv run python scripts/smoke_mcp_gallery.py && uv run python scripts/check_part_envs.py && uv run pytest -q )
}
s_docs() { uvx --with mkdocs-material mkdocs build --strict -d "$(mktemp -d)"; }
s_audit() {
  ( cd apps/api && uv export --frozen --no-dev --no-emit-project --no-hashes -o "${TMPDIR:-/tmp}/ac-req.txt" \
      && uvx pip-audit==2.10.1 -r "${TMPDIR:-/tmp}/ac-req.txt" --disable-pip --no-deps )
  ( cd apps/web && pnpm audit --prod --audit-level high )
}
s_helm() {
  helm lint infra/helm/agentcanvas -f infra/helm/agentcanvas/ci/test-values.yaml --strict
  helm template agentcanvas infra/helm/agentcanvas -f infra/helm/agentcanvas/ci/test-values.yaml >/dev/null
}
s_images() {   # build both images and start the production compose stack; see ci.yml job `images` for the full probes
  docker compose -f infra/compose.local.yml build
  docker compose -f infra/compose.local.yml up -d --wait --wait-timeout 180
  trap 'docker compose -f infra/compose.local.yml down' RETURN
  curl -fsS "http://localhost:${WEB_PORT:-7777}/healthz" >/dev/null || curl -fsS "http://localhost:${WEB_PORT:-7777}/" >/dev/null
}
s_e2e() { make -C "$ROOT" e2e; }

run() {
  case $1 in
    fast) stage fast s_fast ;;
    api-envs) stage api-envs s_api_envs ;;
    api) stage api s_api ;;
    docs) need uvx && stage docs s_docs || skip docs "uvx missing" ;;
    audit) need uvx && stage audit s_audit || skip audit "uvx missing" ;;
    helm) need helm && stage helm s_helm || skip helm "helm missing" ;;
    images) need docker && stage images s_images || skip images "docker missing" ;;
    e2e) stage e2e s_e2e ;;
    default) for s in fast docs audit helm; do run "$s"; done ;;
    full) for s in api-envs api fast docs audit helm images e2e; do run "$s"; done ;;
    *) echo "unknown stage: $1" >&2; exit 2 ;;
  esac
}

for s in "${@:-default}"; do run "$s"; done
printf '\n\033[1mLocal CI\033[0m  passed: %s\n' "${OK[*]:-none}"
[ ${#SKIP[@]} -gt 0 ] && printf 'skipped: %s\n' "${SKIP[*]}"
if [ ${#BAD[@]} -gt 0 ]; then printf '\033[31mFAILED: %s\033[0m\n' "${BAD[*]}"; exit 1; fi
