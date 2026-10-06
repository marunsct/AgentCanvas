#!/usr/bin/env sh
# Logical backup of the AgentCanvas database (control tables, checkpoints and memory) from compose prod (M8-T4).
#   infra/scripts/backup.sh [out-file]          default: agentcanvas-<UTC timestamp>.dump
# ENV_FILE: the settings compose prod was started with (default infra/.env.prod).
# Managed Postgres: rely on its point-in-time recovery and run this nightly as a second, portable copy.
set -eu
out="${1:-agentcanvas-$(date -u +%Y%m%dT%H%M%SZ).dump}"
here=$(dirname "$0")
compose="docker compose -f $here/../compose.prod.yml --env-file ${ENV_FILE:-$here/../.env.prod}"
$compose exec -T postgres pg_dump -U agentcanvas -d agentcanvas --format=custom --no-owner > "$out"
echo "wrote $out ($(wc -c < "$out") bytes)"
