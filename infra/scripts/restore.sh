#!/usr/bin/env sh
# Restore a backup made by backup.sh into compose prod (M8-T4). Stops the API and web first; replaces the database
# and, when the archive is given (or lies beside the dump), the workspaces volume.
#   infra/scripts/restore.sh <file.dump> [workspaces.tar.gz]
# ENV_FILE: the settings compose prod runs with (default infra/.env.prod). It must hold the AGENTCANVAS_SECRET_KEY (or
# AGENTCANVAS_KMS_KEY) and LANGGRAPH_AES_KEY the backup was made with, or secrets and conversations cannot be read.
set -eu
dump="${1:?usage: restore.sh <file.dump> [workspaces.tar.gz]}"
workspaces="${2:-${dump%.dump}.workspaces.tar.gz}"
here=$(dirname "$0")
compose="docker compose -f $here/../compose.prod.yml --env-file ${ENV_FILE:-$here/../.env.prod}"
$compose stop web api
$compose up -d --wait postgres
$compose exec -T postgres dropdb -U agentcanvas --if-exists --force agentcanvas
$compose exec -T postgres createdb -U agentcanvas agentcanvas
# the API's own role (agentcanvas_app, POSTGRES_APP_PASSWORD) before the data: the dump names it as the owner of the
# tables the API made and in its grants, and a new server (another volume, a major upgrade) does not have it yet
$compose run --rm --no-deps -T migrate python -m agentcanvas.db.app_role
$compose exec -T postgres pg_restore -U agentcanvas -d agentcanvas --exit-on-error < "$dump"
if [ -f "$workspaces" ]; then
  $compose run --rm --no-deps -T migrate sh -c 'find /data/workspaces -mindepth 1 -delete && tar -xzf - -C /data/workspaces' \
    < "$workspaces"
  echo "restored the workspaces from $workspaces"
else
  echo "no $workspaces: the workspaces volume is left as it is"
fi
# migrations bring an older backup up to the running version, then the role's grants cover every table again
$compose up -d --wait migrate api web
echo "restored $dump"
