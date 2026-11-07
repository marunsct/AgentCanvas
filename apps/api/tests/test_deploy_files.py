"""The deployment files say what the operator guide says: the Helm chart rendered in its variants (helm template, when
helm is installed) and the compose files read as YAML. Static checks only: nothing is started."""

from __future__ import annotations

import os
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


def env_of(deployment: dict[str, Any], container: int = 0) -> dict[str, Any]:
    spec = deployment["spec"]["template"]["spec"]["containers"][container]
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in spec.get("env", [])}


def compose(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / "infra" / name).read_text())


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
