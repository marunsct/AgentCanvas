#!/usr/bin/env sh
# Restore a backup made by backup.sh into compose prod (M8-T4). Stops the API and web first; replaces the database.
#   infra/scripts/restore.sh <file.dump>
set -eu
dump="${1:?usage: restore.sh <file.dump>}"
here=$(dirname "$0")
compose="docker compose -f $here/../compose.prod.yml --env-file ${ENV_FILE:-$here/../.env.prod}"
$compose stop web api
$compose exec -T postgres dropdb -U agentcanvas --if-exists --force agentcanvas
$compose exec -T postgres createdb -U agentcanvas agentcanvas
$compose exec -T postgres pg_restore -U agentcanvas -d agentcanvas --no-owner --exit-on-error < "$dump"
$compose up -d --wait migrate api web   # migrations bring an older backup up to the running version
echo "restored $dump"
