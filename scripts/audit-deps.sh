#!/usr/bin/env bash
# Known vulnerabilities in every dependency tree that ships: the API's production dependencies (uv.lock), each agent
# framework environment the API image can include (apps/api/framework-locks), the Mastra runtime's npm tree and the
# web app's production dependencies. Dev tools never ship and are not audited. Run by `scripts/ci-local.sh audit`
# and the GitHub workflow's `audit` job. Every tree is checked, then the script fails if any one had a finding.
set -uo pipefail
cd "$(dirname "$0")/.."
PIP_AUDIT=pip-audit==2.10.1
# Advisories accepted, each with the reason; recheck when the package moves.
#   chromadb (in CrewAI's environment, for CrewAI's memory; no fixed release): PYSEC-2026-311, -3813, -3814 and -3815
#   are in chromadb's HTTP server and its RBAC provider. CrewAI uses chromadb embedded, in the worker process; no
#   chromadb server runs anywhere in AgentCanvas.
IGNORE=(--ignore-vuln PYSEC-2026-311 --ignore-vuln PYSEC-2026-3813 --ignore-vuln PYSEC-2026-3814
        --ignore-vuln PYSEC-2026-3815)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
failed=()

echo "== API (uv.lock, production dependencies)"
( cd apps/api && uv export --frozen --no-dev --no-emit-project --no-hashes -q -o "$tmp/api.txt" ) \
  && uvx "$PIP_AUDIT" -r "$tmp/api.txt" --disable-pip --no-deps || failed+=(api)
for lock in apps/api/framework-locks/*.txt; do
  case $lock in *.overrides.txt) continue ;; esac
  name=$(basename "$lock" .txt)
  echo "== $name environment ($lock)"
  uvx "$PIP_AUDIT" -r "$lock" --disable-pip --require-hashes "${IGNORE[@]}" || failed+=("$name")
done
echo "== Mastra runtime (npm, production dependencies)"
( cd apps/api/agentcanvas/adapters/mastra && npm audit --omit=dev --audit-level moderate ) || failed+=(mastra)
echo "== web app (pnpm, production dependencies)"
( cd apps/web && pnpm audit --prod --audit-level high ) || failed+=(web)

if [ ${#failed[@]} -gt 0 ]; then echo "audit: known vulnerabilities in: ${failed[*]}" >&2; exit 1; fi
echo "audit: no known vulnerabilities"
