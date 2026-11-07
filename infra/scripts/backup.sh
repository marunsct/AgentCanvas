#!/usr/bin/env sh
# Backup of AgentCanvas on compose prod (M8-T4): the database (control tables, checkpoints, memory; pg_dump, custom
# format, owners and grants included) and the project workspaces (git repositories, a tar.gz of the volume).
#   infra/scripts/backup.sh [out-file]          default: agentcanvas-<UTC timestamp>.dump
# writes <out-file> and, beside it, <out-file without .dump>.workspaces.tar.gz.
# ENV_FILE: the settings compose prod was started with (default infra/.env.prod).
# The two are taken one after the other, not at one instant: a workspace saved in between may be newer than the dump.
# Keep with them, elsewhere: AGENTCANVAS_SECRET_KEY (or the AGENTCANVAS_KMS_KEY master key) and LANGGRAPH_AES_KEY;
# without them the project secrets and the conversations in the dump cannot be read (docs/user-guide/deploy.md).
# Managed Postgres: rely on its point-in-time recovery and run this nightly as a second, portable copy.
set -eu
out="${1:-agentcanvas-$(date -u +%Y%m%dT%H%M%SZ).dump}"
workspaces="${out%.dump}.workspaces.tar.gz"
here=$(dirname "$0")
compose="docker compose -f $here/../compose.prod.yml --env-file ${ENV_FILE:-$here/../.env.prod}"
$compose exec -T postgres pg_dump -U agentcanvas -d agentcanvas --format=custom > "$out"
# a one-off container of the migrate service: it mounts the workspaces volume, and the API need not be running
$compose run --rm --no-deps -T migrate tar -C /data/workspaces -czf - . > "$workspaces"
echo "wrote $out ($(wc -c < "$out") bytes) and $workspaces ($(wc -c < "$workspaces") bytes)"
