#!/usr/bin/env bash
# The `images` gates of .github/workflows/ci.yml, on this machine: build both images, scan them (Trivy), start the
# production compose stack and probe it through nginx, then the local compose stack. Run by `scripts/ci-local.sh images`.
#
# Everything here lives in compose projects of its own (ac-ci-prod, ac-ci-local) on free ports, with images tagged
# `:ci`, so a running AgentCanvas (make docker-up) is neither touched nor stopped.
set -euo pipefail
cd "$(dirname "$0")/.."

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
PROD_PORT=$(free_port)
LOCAL_PORT=$(free_port)

export API_IMAGE=agentcanvas-api:ci WEB_IMAGE=agentcanvas-web:ci
export POSTGRES_PASSWORD=ci-only POSTGRES_APP_PASSWORD=ci-only-app LANGGRAPH_AES_KEY=ci-only-aes-key-0123456789abcdef
export AGENTCANVAS_SECRET_KEY=ci-only-secret-key-not-for-real-use-0123456789abcdef0123456789abcdef
export AGENTCANVAS_SANDBOX_TOKEN=ci-only-sandbox-token-0123456789abcdef0123
export AGENTCANVAS_EGRESS_SECRET=ci-only-egress-secret-0123456789abcdef0123
export AGENTCANVAS_OIDC_ISSUER=https://login.example.com/realms/ci AGENTCANVAS_OIDC_AUDIENCE=agentcanvas-api
export AGENTCANVAS_OIDC_CLIENT_ID=agentcanvas-web
TRIVY_IMAGE=aquasec/trivy:0.58.1@sha256:ab70a02200597efa04748f210f793936eb647cbcdb0ea69cc30b226d6f5a22c7

# The compose files pin their two internal networks to fixed subnets (the API trusts forwarded addresses from one of
# them), so a second stack on this machine, such as a running `make docker-up`, needs subnets of its own
prod() {
  WEB_PORT=$PROD_PORT SANDBOX_CONTROL_SUBNET=10.251.250.0/24 FRONT_SUBNET=10.251.251.0/24 \
    docker compose -p ac-ci-prod -f infra/compose.prod.yml "$@"
}
local_() {
  WEB_PORT=$LOCAL_PORT SANDBOX_CONTROL_SUBNET=10.252.250.0/24 FRONT_SUBNET=10.252.251.0/24 \
    docker compose -p ac-ci-local -f infra/compose.local.yml "$@"
}
cleanup() { prod down -v >/dev/null 2>&1 || true; local_ down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT
fail() { echo "images: $*" >&2; prod logs --no-color --tail 80 2>&1 | tail -80 >&2 || true; exit 1; }

echo "== build the images"
# the runtime stage is rebuilt every time: its `apt-get upgrade` layer must not come from a cache older than the scan
docker build -q --no-cache-filter runtime -f infra/Dockerfile.api -t "$API_IMAGE" apps/api >/dev/null
docker build -q --no-cache-filter runtime -f infra/Dockerfile.web -t "$WEB_IMAGE" . >/dev/null

echo "== scan them (Trivy: high and critical, with a fix; accepted ones are in infra/.trivyignore)"
for image in "$API_IMAGE" "$WEB_IMAGE"; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD/infra/.trivyignore:/.trivyignore:ro" \
    "$TRIVY_IMAGE" image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed \
    --ignorefile /.trivyignore --exit-code 1 "$image" || fail "Trivy found fixable high or critical vulnerabilities in $image"
done

echo "== start the production stack on port $PROD_PORT"
prod up -d --wait --wait-timeout 240 || fail "the production stack did not become healthy"
B=http://localhost:$PROD_PORT
check() { local what=$1; shift; if ! ( set -o pipefail; "$@" ) >/dev/null 2>&1; then fail "probe failed: $what"; fi; }
sh_() { bash -c "$1"; }

check "the API answers through nginx"          sh_ "curl -fsS $B/api/readyz | grep -q ready"
check "the web app is served"                  sh_ "curl -fsS $B/ | grep -qi '<!doctype html>'"
check "assets are cached as immutable"         sh_ "asset=\$(curl -fsS $B/ | grep -o '/assets/[^\"]*\\.js' | head -1); curl -fsSI \"$B\$asset\" | grep -qi immutable"
check "the CSP names the identity provider"    sh_ "curl -fsSI $B/ | grep -i content-security-policy | grep -q \"connect-src 'self' https://login.example.com;\""
check "the app cannot be framed by others"     sh_ "curl -fsSI $B/ | grep -qi \"frame-ancestors 'self'\""
check "a share page is framed only by its allowed sites" sh_ "curl -fsSI $B/s/sh_ci | grep -i content-security-policy | grep -qi \"frame-ancestors 'self';\""
check "a share page has no X-Frame-Options"    sh_ "test -z \"\$(curl -fsSI $B/s/sh_ci | grep -i x-frame-options)\""
check "the share page is served"               sh_ "curl -fsS $B/s/sh_ci | grep -qi '<!doctype html>'"
check "the chat widget script is served"       sh_ "curl -fsSI $B/widget.js | grep -qi 'content-type: application/javascript'"
check "sign-in is on"                          sh_ "curl -fsS $B/api/v1/auth/config | grep -q '\"mode\":\"oidc\"'"
check "a stranger is refused (401)"            sh_ "test \"\$(curl -s -o /dev/null -w '%{http_code}' $B/api/v1/projects)\" = 401"
check "the API runs as uid 10001"              sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T api id -u | grep -qx 10001"
check "the sandbox runs as uid 10001"          sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T sandbox id -u | grep -qx 10001"
check "the egress proxy runs as uid 10001"     sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T egress id -u | grep -qx 10001"
check "the egress proxy refuses unsigned calls" sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T sandbox python -c \"import socket; s=socket.create_connection(('egress', 3128), 3); s.sendall(b'CONNECT example.com:443 HTTP/1.1\\r\\n\\r\\n'); assert b' 407 ' in s.recv(200)\""
check "the sandbox holds none of the API's secrets" sh_ "test -z \"\$(docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T sandbox env | grep -E 'DATABASE_URL|SECRET_KEY|AES_KEY|API_KEY')\""
check "the sandbox has no route out but the proxy" sh_ "! docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T sandbox python -c \"import socket; socket.create_connection(('1.1.1.1', 443), 3)\""
check "the API's database role is an ordinary one (row-level security applies)" sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T postgres psql -U agentcanvas -tAc \"SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'agentcanvas_app'\" | grep -qx f"
check "metrics are not served through nginx"   sh_ "test \"\$(curl -s -o /dev/null -w '%{http_code}' $B/api/metrics)\" = 404"
check "metrics exist inside the API"           sh_ "docker compose -p ac-ci-prod -f infra/compose.prod.yml exec -T api python -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/metrics').read().decode())\" | grep -q agentcanvas_runs_active"
echo "production stack: all probes passed"
prod down -v >/dev/null

echo "== start the local stack on port $LOCAL_PORT (same images, no sign-in, first-run setup)"
local_ up -d --no-build --wait --wait-timeout 240 || { local_ logs --no-color --tail 80 >&2; echo "images: the local stack did not become healthy" >&2; exit 1; }
L=http://localhost:$LOCAL_PORT
check "dev mode"                               sh_ "curl -fsS $L/api/v1/auth/config | grep -q '\"mode\":\"dev\"'"
check "first-run setup is offered"             sh_ "curl -fsS $L/api/v1/projects/prj_default/setup | grep -q '\"done\":false'"
check "model providers are listed"             sh_ "curl -fsS $L/api/v1/model-providers | grep -q '\"id\":\"ollama\"'"
check "setup can be completed"                 sh_ "curl -fsS -X PUT -H 'content-type: application/json' -d '{\"model\": \"ollama:llama3.3\"}' $L/api/v1/projects/prj_default/setup | grep -q '\"done\":true'"
# a real share link through nginx's auth_request: its allowed sites (and only those) end up in the page's frame-ancestors
python3 - "$L" <<'PY' || { echo "images: share-link framing through nginx failed" >&2; exit 1; }
import json, sys, urllib.request

base = sys.argv[1]


def call(method, path, body=None):
    req = urllib.request.Request(base + "/api/v1" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def csp(path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return r.headers.get("Content-Security-Policy", "")


wf = call("POST", "/projects/prj_default/workflows", {"name": "framing", "template": "chat_assistant"})
dep = call("POST", f"/workflows/{wf['id']}/publish", {})["deployment"]["id"]
link = call("PUT", f"/deployments/{dep}/share", {"enabled": True, "frame_origins": ["https://www.example.com", "https://*.partner.test"]})
page = csp(link["path"])
assert "frame-ancestors 'self' https://www.example.com https://*.partner.test;" in page, page  # the listed sites plus the app
assert "frame-ancestors 'self';" in csp("/s/sh_does_not_exist"), "an unknown link must allow only the app"
open_link = call("PUT", f"/deployments/{dep}/share", {"enabled": True, "frame_origins": []})
assert "frame-ancestors *;" in csp(open_link["path"]), "a link with no list may be framed by any site"
print("share links: the allowed sites reach the page's frame-ancestors")
PY
# workspace code in the sandbox service reaches the hosts its sandbox.json lists (through the egress proxy), and only those
python3 - "$L" <<'PY' || { local_ logs --no-color --tail 40 egress sandbox >&2; echo "images: sandbox egress failed" >&2; exit 1; }
import json, sys, urllib.request

base = sys.argv[1]
SCRIPT = """
import socket, urllib.request


def run(state):
    out = {}
    for name, url in (("listed", "https://pypi.org/simple/"), ("other", "https://example.com/")):
        try:
            out[name] = urllib.request.urlopen(url, timeout=20).status
        except Exception as e:
            out[name] = str(e)[:120]
    try:
        socket.create_connection(("1.1.1.1", 443), 3)
        out["direct"] = "reached"
    except Exception as e:
        out["direct"] = type(e).__name__
    return {"out": json.dumps(out)}
""".replace("import socket", "import json, socket")


def call(method, path, body=None, raw=False):
    req = urllib.request.Request(base + "/api/v1" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "If-Match": "1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode() if raw else json.load(r)


call("PUT", "/projects/prj_default/files/sandbox.json", {"content": json.dumps({"egress": ["pypi.org"]})})
call("PUT", "/projects/prj_default/files/scripts/egress_probe.py", {"content": SCRIPT})
wf = call("POST", "/projects/prj_default/workflows", {"name": "egress probe"})
ir = {"id": wf["id"], "name": "egress probe", "state": {"channels": [{"name": "out"}]},
      "nodes": [{"id": "n_s", "type": "core.script", "name": "probe", "config": {"import_path": "scripts.egress_probe:run"}}],
      "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
                {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "END"}}]}
call("PUT", f"/workflows/{wf['id']}", {"ir": ir})
stream = call("POST", f"/workflows/{wf['id']}/runs/stream", {"input": {}}, raw=True)
last = json.loads([ln[5:] for ln in stream.splitlines() if ln.startswith("data:")][-1])
out = json.loads(last["output"]["out"])
assert out["listed"] == 200, out
assert "403" in str(out["other"]), out
assert out["direct"] != "reached", out
call("PUT", "/projects/prj_default/files/sandbox.json", {"content": "{}"})
print("sandbox egress: the listed host answers, others are refused, nothing else is reachable")
PY
echo "local stack: all probes passed"
