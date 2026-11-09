"""The deployment files say what the operator guide says: the Helm chart rendered in its variants (helm template, when
helm is installed) and the compose files read as YAML. Static checks only: nothing is started."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "infra" / "helm" / "agentcanvas"
CI_VALUES = CHART / "ci" / "test-values.yaml"
HELM = shutil.which("helm") or next(
    (str(p) for p in [Path.home() / ".local" / "bin" / "helm"] if os.access(p, os.X_OK)), None
)
needs_helm = pytest.mark.skipif(HELM is None, reason="helm is not installed")


def helm(*sets: str, values: Path | None = CI_VALUES) -> subprocess.CompletedProcess[str]:
    args = [str(HELM), "template", "agentcanvas", str(CHART)]
    if values:
        args += ["-f", str(values)]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, capture_output=True, text=True, timeout=60, check=False)


def render(*sets: str) -> list[dict[str, Any]]:
    done = helm(*sets)
    assert done.returncode == 0, done.stderr
    return [d for d in yaml.safe_load_all(done.stdout) if d]


def find(docs: list[dict[str, Any]], kind: str, suffix: str) -> dict[str, Any] | None:
    return next((d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(suffix)), None)


def pod_spec(docs: list[dict[str, Any]], suffix: str, kind: str = "Deployment") -> dict[str, Any]:
    doc = find(docs, kind, suffix)
    assert doc, f"no {kind} *{suffix}"
    return doc["spec"]["template"]["spec"]


def env_of(deployment: dict[str, Any], container: int = 0) -> dict[str, Any]:
    spec = deployment["spec"]["template"]["spec"]["containers"][container]
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in spec.get("env", [])}


def compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "infra" / name).read_text())


def test_every_page_location_sends_hsts_over_https_and_a_permissions_policy():
    """HARDENING 2a: the web image's pages send Strict-Transport-Security when the request came over https (an empty
    value otherwise, which nginx leaves out) and a Permissions-Policy that turns off what the app never uses."""
    import re

    template = (ROOT / "infra" / "nginx.conf").read_text()
    hsts = re.search(r"map \$forwarded_proto \$hsts \{(.*?)\}", template, re.S)
    assert hsts is not None and re.search(r'https\s+"max-age=31536000";', hsts.group(1))
    assert re.search(r'default\s+"";', hsts.group(1))  # plain http: no header
    pages = [b for b in re.split(r"\n  location ", template) if "Content-Security-Policy" in b]
    assert len(pages) >= 4
    for block in pages:
        assert "add_header Strict-Transport-Security $hsts always;" in block, block[:80]
        assert re.search(r'add_header Permissions-Policy "camera=\(\), microphone=\(\), geolocation=\(\)', block)


@needs_helm
def test_the_chart_signs_in_with_oidc_by_default_and_with_matura_when_asked():
    api = find(render(), "Deployment", "-api")
    assert api and env_of(api)["AGENTCANVAS_AUTH_MODE"] == "oidc"
    assert not any(k.startswith("AGENTCANVAS_MATURA_") for k in env_of(api))
    docs = render("config.authMode=matura", "config.matura.url=https://matura.example.com",
                  "config.matura.publicUrl=https://matura.example.com")  # fmt: skip
    for kind, name in [("Deployment", "-api"), ("Job", "-migrate-1")]:
        doc = find(docs, kind, name)
        assert doc, name
        env = env_of(doc)
        assert env["AGENTCANVAS_AUTH_MODE"] == "matura"
        assert env["AGENTCANVAS_MATURA_URL"] == env["AGENTCANVAS_MATURA_PUBLIC_URL"] == "https://matura.example.com"
    bad = helm("config.authMode=dev")
    assert bad.returncode != 0 and "config.authMode must be oidc or matura" in bad.stderr


@needs_helm
def test_remote_script_execution_without_the_sandbox_service_is_refused():
    bad = helm("sandbox.enabled=false")
    assert bad.returncode != 0 and "set sandbox.enabled=true" in bad.stderr
    docs = render("sandbox.enabled=false", "config.scriptExecution=docker")
    assert not find(docs, "Deployment", "-sandbox") and not find(docs, "Deployment", "-egress")
    api = find(docs, "Deployment", "-api")
    assert api and "AGENTCANVAS_SANDBOX_URL" not in env_of(api)


@needs_helm
def test_no_published_image_is_assumed_and_the_secret_message_names_every_required_key():
    bad = helm("image.api.repository=")
    assert bad.returncode != 0 and "image.api.repository is required" in bad.stderr
    bad = helm("secrets.existingSecret=")
    assert bad.returncode != 0
    for key in ["DATABASE_URL", "AGENTCANVAS_SECRET_KEY", "LANGGRAPH_AES_KEY", "AGENTCANVAS_SANDBOX_TOKEN",
                "AGENTCANVAS_EGRESS_SECRET"]:  # fmt: skip
        assert key in bad.stderr, key
    bad = helm("secrets.existingSecret=", "sandbox.egress.enabled=false")
    assert "AGENTCANVAS_SANDBOX_TOKEN" in bad.stderr and "AGENTCANVAS_EGRESS_SECRET" not in bad.stderr
    api = find(render(), "Deployment", "-api")
    assert api and api["spec"]["template"]["spec"]["containers"][0]["image"].startswith(
        "registry.example.com/agentcanvas-api:"
    )


def test_the_operator_guide_lists_every_key_the_chart_requires():
    guide = (ROOT / "docs" / "user-guide" / "deploy.md").read_text()
    helpers = (CHART / "templates" / "_helpers.tpl").read_text()
    for key in ["DATABASE_URL", "AGENTCANVAS_SECRET_KEY", "LANGGRAPH_AES_KEY", "AGENTCANVAS_SANDBOX_TOKEN",
                "AGENTCANVAS_EGRESS_SECRET"]:  # fmt: skip
        assert f'"{key}"' in helpers and f"| `{key}` |" in guide, key


@needs_helm
def test_components_with_two_or_more_replicas_get_a_disruption_budget_and_spread_across_nodes():
    docs = render()
    for component in ["api", "web", "sandbox", "egress"]:
        pdb = find(docs, "PodDisruptionBudget", f"-{component}")
        assert pdb and pdb["spec"]["minAvailable"] == 1, component
        assert pdb["spec"]["selector"]["matchLabels"]["app.kubernetes.io/component"] == component
        spread = pod_spec(docs, f"-{component}")["topologySpreadConstraints"]
        assert spread[0]["topologyKey"] == "kubernetes.io/hostname", component
        assert spread[0]["labelSelector"]["matchLabels"]["app.kubernetes.io/component"] == component
    docs = render("api.replicas=1", "topologySpread.enabled=false")  # one replica: a budget would block every drain
    assert not find(docs, "PodDisruptionBudget", "-api") and find(docs, "PodDisruptionBudget", "-web")
    assert "topologySpreadConstraints" not in pod_spec(docs, "-api")


def trusted(value: str | None, var: str = "TRUSTED_PROXIES_REAL_IP") -> subprocess.CompletedProcess[str]:
    """The web image's start-up step that turns TRUSTED_PROXIES into nginx's set_real_ip_from directives and the
    entries of the geo block that decides whether X-Forwarded-Proto is believed."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")} | ({"TRUSTED_PROXIES": value} if value is not None else {})
    script = ROOT / "infra" / "web" / "19-trusted-proxies.envsh"
    return subprocess.run(["sh", "-c", f'. "{script}" && printf %s "${var}"'], capture_output=True,
                          text=True, env=env, timeout=10, check=False)  # fmt: skip


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell")
def test_the_web_image_trusts_every_listed_proxy_range_and_nothing_else():
    assert trusted(None).stdout == "set_real_ip_from 127.0.0.1/32; "
    assert trusted("10.0.0.0/8").stdout == "set_real_ip_from 10.0.0.0/8; "
    many = "10.0.0.0/8 172.16.0.0/12,192.168.0.0/16"
    assert trusted(many).stdout == (
        "set_real_ip_from 10.0.0.0/8; set_real_ip_from 172.16.0.0/12; set_real_ip_from 192.168.0.0/16; "
    )
    # the geo block takes "range value;" entries: several ranges once broke the start of nginx ("invalid number
    # of the geo parameters") with the chart's default of three
    assert trusted(many, "TRUSTED_PROXIES_GEO").stdout == "10.0.0.0/8 1; 172.16.0.0/12 1; 192.168.0.0/16 1; "
    bad = trusted("10.0.0.0/8; return 200")
    assert bad.returncode != 0 and "not an address range" in bad.stderr
    template = (ROOT / "infra" / "nginx.conf").read_text()
    assert "${TRUSTED_PROXIES_REAL_IP}" in template and "${TRUSTED_PROXIES_GEO}" in template
    assert "${TRUSTED_PROXIES}" not in template  # the raw list is never pasted into a directive


@needs_helm
def test_the_chart_trusts_every_private_range_by_default():
    web = find(render(), "Deployment", "-web")
    assert web and env_of(web)["TRUSTED_PROXIES"] == "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16"


@needs_helm
def test_the_sandbox_reaches_the_egress_proxy_only_and_no_dns():
    docs = render()
    policy = find(docs, "NetworkPolicy", "-sandbox")
    assert policy
    (rule,) = policy["spec"]["egress"]
    assert rule["ports"] == [{"port": 3128}]
    assert rule["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "egress"
    api = find(docs, "Deployment", "-api")
    assert api and env_of(api)["AGENTCANVAS_EGRESS_PROXY_RESOLVE"] == "true"  # the API hands over the ClusterIP
    offline = find(render("sandbox.egress.enabled=false"), "NetworkPolicy", "-sandbox")
    assert offline and offline["spec"]["egress"] == []


# the variables compose.prod.yml requires, with throwaway values, for `docker compose config`
PROD_ENV = {
    "POSTGRES_PASSWORD": "x", "POSTGRES_APP_PASSWORD": "y", "AGENTCANVAS_SECRET_KEY": "k" * 64,
    "LANGGRAPH_AES_KEY": "a" * 32, "AGENTCANVAS_SANDBOX_TOKEN": "t" * 32, "AGENTCANVAS_EGRESS_SECRET": "e" * 32,
}  # fmt: skip


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
@pytest.mark.parametrize("name", ["compose.prod.yml", "compose.local.yml", "docker-compose.yml"])
def test_the_compose_files_are_valid(name):
    """`docker compose config` only reads the file: nothing is started."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp")} | PROD_ENV
    done = subprocess.run(["docker", "compose", "-f", str(ROOT / "infra" / name), "config", "-q"], env=env,
                          capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(("name", "env_file"), [("compose.prod.yml", ".env.prod"), ("compose.local.yml", ".env.local")])
def test_the_api_reads_every_setting_of_the_env_file_and_the_sandbox_none(name, env_file):
    services = compose(name)["services"]
    api_files = [f["path"] for f in services["api"]["env_file"]]
    assert api_files == [env_file] and services["api"]["env_file"][0]["required"] is False
    for other in ["sandbox", "egress", "postgres", "web"]:
        assert "env_file" not in services[other], other  # the sandbox and the proxy get only their own secret
    assert services["egress"]["environment"]["AGENTCANVAS_EGRESS_ALLOW_PRIVATE"].startswith("${")
    if name == "compose.prod.yml":
        assert [f["path"] for f in services["migrate"]["env_file"]] == [env_file]
        assert services["api"]["environment"]["POSTGRES_PASSWORD"] == ""  # the owner's password is not the API's


def test_the_production_api_runs_locked_down_and_the_web_port_stays_on_this_host():
    prod = compose("compose.prod.yml")["services"]
    for name in ["api", "migrate"]:
        svc = prod[name]
        assert svc["read_only"] == "${API_READ_ONLY:-true}" and svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"] and svc["mem_limit"] and svc["pids_limit"], name
        assert {t.split(":")[0] for t in svc["tmpfs"]} == {"/tmp", "/home/app"}, name
    assert prod["web"]["ports"] == ["${WEB_BIND:-127.0.0.1}:${WEB_PORT:-8080}:8080"]  # plain HTTP: not 0.0.0.0
    for name in ["compose.prod.yml", "compose.local.yml"]:
        image = compose(name)["services"]["postgres"]["image"]
        assert "pgvector/pgvector:pg16@sha256:" in image, name  # a digest, not a tag that moves


def test_the_development_services_listen_on_this_computer_only():
    services = compose("docker-compose.yml")["services"]
    assert "redis" not in services  # nothing in the API uses it
    for name, svc in services.items():
        for port in svc.get("ports", []):
            assert port.startswith("127.0.0.1:"), (name, port)  # development passwords: never on the network
    assert "redis" not in (ROOT / "Makefile").read_text().split("\ninfra:", 1)[1].split("\n\n", 1)[0]


def test_backups_hold_the_workspaces_and_a_restore_creates_the_api_role_before_the_data():
    scripts = ROOT / "infra" / "scripts"
    for script in ["backup.sh", "restore.sh"]:
        done = subprocess.run(["sh", "-n", str(scripts / script)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
    backup, restore = (scripts / "backup.sh").read_text(), (scripts / "restore.sh").read_text()
    assert "pg_dump" in backup and "tar -C /data/workspaces -czf -" in backup
    assert "--no-owner" not in backup  # the owners of the API's own tables come back with the data
    role, data = restore.index("agentcanvas.db.app_role"), restore.index("pg_restore")
    assert role < data < restore.index("tar -xzf - -C /data/workspaces") < restore.index("up -d --wait migrate api")
    for key in ["AGENTCANVAS_SECRET_KEY", "AGENTCANVAS_KMS_KEY", "LANGGRAPH_AES_KEY"]:
        assert key in backup and f"`{key}`" in (ROOT / "docs" / "user-guide" / "deploy.md").read_text(), key


def test_the_alerts_watch_series_the_api_exports_and_link_to_runbooks_that_exist():
    import re

    from agentcanvas import observability

    rules = yaml.safe_load((ROOT / "infra" / "observability" / "alerts.yaml").read_text())["groups"][0]["rules"]
    down = next(r for r in rules if r["alert"] == "AgentCanvasApiDown")
    assert "job=" not in down["expr"]  # the scrape job's name is the operator's (kubernetes-pods), not ours
    (metric,) = re.findall(r"absent\((\w+)\)", down["expr"])
    assert f"\n{metric} " in observability.generate_latest().decode()  # exported unlabelled, from start-up
    guide = (ROOT / "docs" / "user-guide" / "operations.md").read_text()
    slugs = {
        re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in re.findall(r"^#+ (.+)$", guide, re.M)
    }
    for rule in rules:
        if link := rule["annotations"].get("runbook"):
            assert link.split("#", 1)[1] in slugs, (rule["alert"], link)
    assert "| API pods crash-loop at startup" in guide and "JWKS outage" in guide


def test_postgres_accepts_every_connection_the_default_api_replicas_may_open():
    import re

    from agentcanvas.adapters.langchain.persistence import POOL_SIZE
    from agentcanvas.settings import Settings

    prod = compose("compose.prod.yml")["services"]
    command = " ".join(prod["postgres"]["command"])
    allowed = int(re.search(r"max_connections=\$\{POSTGRES_MAX_CONNECTIONS:-(\d+)\}", command).group(1))  # type: ignore[union-attr]
    replicas = int(re.search(r":-(\d+)", str(prod["api"]["deploy"]["replicas"])).group(1))  # type: ignore[union-attr]
    pool, overflow = (Settings.model_fields[f].default for f in ("db_pool_size", "db_max_overflow"))
    per_replica = pool + overflow + POOL_SIZE
    assert replicas * per_replica + 10 <= allowed, (replicas, per_replica, allowed)
    guide = (ROOT / "docs" / "user-guide" / "deploy.md").read_text()
    assert f"{pool} + {overflow} + {POOL_SIZE} = {per_replica}" in guide  # the guide's sum is the code's defaults


def test_the_nightly_copilot_evaluation_uses_a_listed_model_and_says_when_it_skips():
    import json
    import re

    workflow = (ROOT / ".github" / "workflows" / "copilot-evals.yml").read_text()
    (default,) = re.findall(r"vars\.COPILOT_EVAL_MODEL \|\| '([^']+)'", workflow)
    catalog = json.loads((ROOT / "apps" / "api" / "agentcanvas" / "registry" / "models.json").read_text())
    models = catalog if isinstance(catalog, list) else catalog.get("models", catalog)
    assert default == "anthropic:claude-haiku-5-5" and default in json.dumps(models)
    assert "title=Copilot evaluation skipped" in workflow and "GITHUB_STEP_SUMMARY" in workflow


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_local_ci_reports_a_stage_that_left_checks_out_as_skipped_not_passed():
    script = (ROOT / "scripts" / "ci-local.sh").read_text()
    head = script[script.index("declare -a OK") : script.index("\nskip()")]  # the bookkeeping, not the stages
    probe = (
        head
        + """
whole() { true; }
part() { partial "charts not linted"; }
broken() { false; }
stage whole whole; stage part part; stage broken broken
printf 'OK=%s|SKIP=%s|BAD=%s' "${OK[*]}" "${SKIP[*]}" "${BAD[*]}"
"""
    )
    done = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, timeout=30, check=False)
    out = done.stdout.rsplit("\n", 1)[-1]
    assert out == "OK=whole|SKIP=part (passed in part: charts not linted)|BAD=broken", done.stdout + done.stderr
    assert 'partial "SQLite only, no Postgres"' in script and script.count('partial "') >= 4


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_local_ci_fails_a_stage_when_any_of_its_commands_fails_not_only_the_last():
    """bash ignores `set -e` inside an `if` condition: a stage of two commands used to pass when the first failed
    (pip-audit's findings, `helm lint --strict`), because only the last one's status counted."""
    script = (ROOT / "scripts" / "ci-local.sh").read_text()
    head = script[script.index("declare -a OK") : script.index("\nskip()")]
    probe = (
        head
        + """
first_fails() { ( false ); ( true ); }
func_fails() { f() { false; echo after; true; }; f; }
fine() { ( true ); ( true ); }
stage first first_fails; stage func func_fails; stage fine fine
printf 'OK=%s|BAD=%s' "${OK[*]}" "${BAD[*]}"
"""
    )
    done = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, timeout=30, check=False)
    out = done.stdout.rsplit("\n", 1)[-1]
    assert out == "OK=fine|BAD=first func", done.stdout + done.stderr
    assert "after" not in done.stdout  # the stage stops at its first failure


def test_ci_runs_actions_by_commit_and_the_docs_build_with_pinned_versions():
    import re

    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in workflow.read_text().splitlines():
            if "uses:" in line:  # a tag can be moved to other code; a commit cannot
                assert re.search(r"@[0-9a-f]{40}  # v\d+\.\d+\.\d+", line), (workflow.name, line)
    for path in [".github/workflows/ci.yml", "scripts/ci-local.sh", "Makefile"]:
        text = (ROOT / path).read_text()
        assert "mkdocs-material" in text and "--with mkdocs-material " not in text, path
        assert "mkdocs==1.6.1" in text and "mkdocs-material==9.8.1" in text, path


@pytest.mark.parametrize("size", [16, 24, 32])
def test_every_aes_key_size_the_guides_allow_encrypts_checkpoints(monkeypatch, size):
    from agentcanvas.adapters.langchain.persistence import checkpoint_serde

    monkeypatch.setenv("LANGGRAPH_AES_KEY", "k" * size)
    serde = checkpoint_serde()
    kind, blob = serde.dumps_typed({"a": 1})
    assert "aes" in kind and serde.loads_typed((kind, blob)) == {"a": 1}
    for path in ["docs/user-guide/deploy.md", "docs/user-guide/operations.md",
                 "infra/.env.prod.example"]:  # fmt: skip
        assert "exactly 32" not in (ROOT / path).read_text(), path


@needs_helm
@pytest.mark.parametrize(
    "sets",
    [
        ("sandbox.enabled=false", "config.scriptExecution=subprocess"),
        ("sandbox.egress.enabled=false",),
        ("networkPolicy.enabled=false",),
        ("config.authMode=matura", "config.matura.url=https://matura.example.com"),
        ("migrations.enabled=false", "workspaces.persistence.enabled=false", "podDisruptionBudget.enabled=false"),
    ],
)
def test_the_chart_renders_in_each_variant(sets):
    docs = render(*sets)
    kinds = {d["kind"] for d in docs}
    assert {"Deployment", "Service"} <= kinds
    api = find(docs, "Deployment", "-api")
    assert api and env_of(api)["AGENTCANVAS_AUTH_MODE"] in ("oidc", "matura")
    if "networkPolicy.enabled=false" in sets:
        assert "NetworkPolicy" not in kinds
    if "sandbox.egress.enabled=false" in sets:
        assert not find(docs, "Deployment", "-egress") and "AGENTCANVAS_EGRESS_PROXY" not in env_of(api)


def test_the_guide_says_how_to_upgrade_and_scale_each_way_of_running():
    guide = (ROOT / "docs" / "user-guide" / "deploy.md").read_text()
    upgrades = guide.split("## Upgrades", 1)[1].split("\n## ", 1)[0]
    assert "docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait" in upgrades
    assert "every API replica at once" in upgrades and "helm upgrade" in upgrades
    assert "## Scaling" in guide and "API_REPLICAS" in guide.split("## Scaling", 1)[1]


def test_the_reference_schema_names_the_folder_the_migrations_are_in():
    import re

    for path in ["infra/schema.sql", "scripts/export_schema_sql.sh"]:
        (folder,) = re.findall(r"Alembic migrations \((apps/api/[\w/]+)\)", (ROOT / path).read_text())
        assert (ROOT / folder).is_dir() and any((ROOT / folder).glob("*.py")), (path, folder)


def test_the_audit_covers_every_dependency_tree_the_images_ship_and_the_scan_sees_them():
    """The audit read only uv.lock and the web app, and the scanned API image was built without FRAMEWORKS or Mastra,
    so the framework environments and the Mastra npm tree the Docker app ships were never checked by either gate."""
    audit = (ROOT / "scripts" / "audit-deps.sh").read_text()
    assert "apps/api/framework-locks/*.txt" in audit and "--require-hashes" in audit
    assert "adapters/mastra && npm audit --omit=dev --audit-level moderate" in audit
    assert "pnpm audit --prod --audit-level moderate" in audit  # fast-uri's moderate advisories passed at high
    assert "scripts/audit-deps.sh" in (ROOT / "scripts" / "ci-local.sh").read_text()
    assert "scripts/audit-deps.sh" in (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    images = re.sub(r"\\\n\s*", "", (ROOT / "scripts" / "ci-images.sh").read_text())  # one line per command
    build = next(line for line in images.splitlines() if "Dockerfile.api" in line and "docker build" in line)
    assert "--build-arg FRAMEWORKS=all" in build and "--build-arg WITH_MASTRA=true" in build


def test_the_web_image_installs_with_the_web_apps_pnpm_settings():
    """pnpm-workspace.yaml holds the web app's overrides (fast-uri past its advisories): an image that installed without
    it would fail the frozen install, or with a looser lock ship the vulnerable release."""
    copy = next(
        line for line in (ROOT / "infra" / "Dockerfile.web").read_text().splitlines() if "pnpm-lock.yaml" in line
    )
    assert "apps/web/pnpm-workspace.yaml" in copy
    settings = yaml.safe_load((ROOT / "apps" / "web" / "pnpm-workspace.yaml").read_text())
    assert settings["overrides"]["fast-uri@>=3.0.0 <3.1.9"] == "3.1.9"


def test_the_web_image_ci_and_developers_run_the_pnpm_package_json_names():
    """The web image took whatever pnpm 10.x was newest on the day of the build (`corepack prepare pnpm@10`), CI took
    `version: 10`, and nothing checked what was downloaded: the same commit could install differently a week later."""
    manager = json.loads((ROOT / "apps" / "web" / "package.json").read_text()).get("packageManager", "")
    assert re.fullmatch(r"pnpm@\d+\.\d+\.\d+\+sha512\.[0-9a-f]{128}", manager), manager
    web = (ROOT / "infra" / "Dockerfile.web").read_text()
    assert "corepack install" in web and "pnpm@" not in web and "PNPM_VERSION" not in web
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "version: 10" not in workflow and workflow.count("package_json_file: apps/web/package.json") == 3


def test_dependency_updates_wait_for_a_new_release_to_age_before_taking_it():
    """A hijacked release is usually found and pulled within days. Without a cooldown, that week's Dependabot pull
    request could carry it, and local CI cannot know a release is malicious before an advisory exists. Security
    updates are not delayed by a cooldown."""
    updates = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())["updates"]
    late = [
        u["directory" if "directory" in u else "directories"]
        for u in updates
        if u.get("cooldown", {}).get("default-days", 0) < 7
    ]
    assert not late, f"no 7-day cooldown: {late}"
    pnpm = yaml.safe_load((ROOT / "apps" / "web" / "pnpm-workspace.yaml").read_text())
    assert pnpm.get("minimumReleaseAge", 0) >= 7 * 24 * 60  # minutes; resolving anew locally (pnpm add, pnpm update)


@needs_helm
def test_every_pod_runs_without_a_service_account_token_and_on_a_read_only_root():
    """The API, web and migrate pods mounted a Kubernetes API token and could write anywhere in the image, while
    compose prod ran the API read-only and the chart's sandbox and egress pods already had both: a code-execution
    bug in the API (it parses imports, PDFs and OCR input) could plant files in the image or call the cluster's API."""
    docs = render()
    pods = [(d["metadata"]["name"], d["spec"]["template"]["spec"]) for d in docs if d["kind"] in ("Deployment", "Job")]
    assert {name.rsplit("-", 1)[-1] for name, _ in pods} >= {"api", "web", "sandbox", "egress"}
    for name, spec in pods:
        assert spec.get("automountServiceAccountToken") is False, name
        assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}, name
        for c in spec["containers"]:
            security = c["securityContext"]
            assert security.get("readOnlyRootFilesystem") is True, f"{name}/{c['name']}"
            assert security["capabilities"]["drop"] == ["ALL"] and security["allowPrivilegeEscalation"] is False
    mounts = {m["mountPath"]: m["name"] for m in pod_spec(docs, "-api")["containers"][0]["volumeMounts"]}
    assert {"/tmp", "/home/app"} <= set(mounts)  # what compose prod gives it as tmpfs
    assert {"/tmp", "/etc/nginx/conf.d"} <= {
        m["mountPath"] for m in pod_spec(docs, "-web")["containers"][0]["volumeMounts"]
    }
    # no recursive chown of every workspace's git objects on each API pod start
    assert pod_spec(docs, "-api")["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"
    off = render("api.readOnlyRootFilesystem=false")
    assert pod_spec(off, "-api")["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is False


def test_the_web_bundle_ships_its_licence_notices_and_each_image_gets_an_sbom():
    """The minified bundle dropped every licence comment, so React, xyflow and CodeMirror (MIT) and elkjs (EPL-2.0)
    shipped without the notices their licences require, and no image had a bill of materials for a customer's review."""
    vite = (ROOT / "apps" / "web" / "vite.config.ts").read_text()
    assert 'license: { fileName: "third-party-licenses.txt" }' in vite
    images = (ROOT / "scripts" / "ci-images.sh").read_text()
    assert "/third-party-licenses.txt" in images  # probed on the running web image
    assert "--format cyclonedx" in images and ".cdx.json" in images
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--format cyclonedx" in workflow and "/third-party-licenses.txt" in workflow


def test_the_gates_run_the_python_the_api_image_ships():
    """With no .python-version, `uv sync` and `uv run` (and so the suite, ruff and pyright, locally and in CI) used
    uv's default Python, 3.13, while the image runs 3.14: a change could pass every gate and fail only in the image."""
    image = re.search(r"^FROM python:(\d+\.\d+)-slim", (ROOT / "infra" / "Dockerfile.api").read_text(), re.M)
    pinned = ROOT / "apps" / "api" / ".python-version"
    assert image and pinned.is_file() and pinned.read_text().strip() == image.group(1)


def test_the_pooler_runbook_fits_the_driver_and_the_row_level_security_the_api_uses():
    """The runbook advised asyncpg settings (the API uses psycopg) and said transaction pooling just works, while
    PgBouncer refuses the startup option that lets background work see every organisation."""
    row = next(
        line
        for line in (ROOT / "docs" / "user-guide" / "operations.md").read_text().splitlines()
        if line.startswith("| DB connection exhaustion")
    )
    assert "asyncpg" not in row and "statement_cache_size" not in row
    for needed in ("max_prepared_statements", "ignore_startup_parameters = options", "agentcanvas.all_tenants"):
        assert needed in row, needed
    assert "AGENTCANVAS_PERSISTENCE_URL" in row  # its startup holds a session lock, which transaction pooling breaks


def test_the_documented_production_builds_include_every_framework_like_the_docker_app():
    """The Docker app builds with every framework; the production commands built without the arguments, so the image
    held only LangGraph and the OpenAI Agents SDK and CrewAI, ADK, Mastra... workflows failed at run time."""
    local = yaml.safe_load((ROOT / "infra" / "compose.local.yml").read_text())["services"]["api"]["build"]["args"]
    assert local["FRAMEWORKS"] == "${FRAMEWORKS:-all}" and local["WITH_MASTRA"] == "${WITH_MASTRA:-true}"
    for path in ["docs/user-guide/deploy.md", "infra/compose.prod.yml"]:
        builds = [
            line for line in (ROOT / path).read_text().splitlines() if "docker build -f infra/Dockerfile.api" in line
        ]
        assert builds, path
        for line in builds:
            assert "--build-arg FRAMEWORKS=all" in line and "--build-arg WITH_MASTRA=true" in line, (path, line)
