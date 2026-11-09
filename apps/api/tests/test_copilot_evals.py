"""`make copilot-evals`: the harness on this computer, with the offline demo Copilot when no model can be reached."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from agentcanvas.copilot import evals

ROOT = Path(__file__).parents[3]


def test_a_model_that_cannot_be_reached_says_why(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert evals.unusable("anthropic:claude-haiku-4-5") == "ANTHROPIC_API_KEY is not set"
    assert "not a model provider" in (evals.unusable("nope:x") or "")
    assert "not running" in (evals.unusable("ollama:qwen3", ollama_up=lambda: False) or "")
    assert evals.unusable("ollama:qwen3", ollama_up=lambda: True) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert evals.unusable("anthropic:claude-haiku-4-5") is None
    assert evals.unusable(evals.DEMO) is None


def test_a_debug_fix_that_deletes_the_broken_step_fails_and_an_unknown_case_is_an_error(monkeypatch):
    """A fix that deletes the broken step leaves no validation error and passed; `--case` with a typo ran 0 cases,
    reported 0/0 and exited 0."""
    from agentcanvas.ir.commands import apply_batch, parse_commands
    from agentcanvas.registry import default_registry
    from agentcanvas.settings import Settings

    registry = default_registry()
    case = next(c for c in evals.debug_cases(Settings(env="test"), registry) if c["id"].endswith("/bad_output"))
    start = case["start"]
    assert set(case["keep"]) == {n.id for n in start.nodes}
    broken = next(n for n in start.nodes if n.config.get("output") == "nowhere")
    removed_edges = [e.id for e in start.edges if broken.id in (e.source.node, e.target.node)]
    gutted, _ = apply_batch(
        start, parse_commands([*({"op": "Disconnect", "id": e} for e in removed_edges),
                               {"op": "RemoveNode", "id": broken.id}])
    )  # fmt: skip
    verdict = evals.judge(case, gutted, registry, evals.CaseResult(id=case["id"], passed=False))
    assert not verdict.passed and verdict.removed == [broken.id]

    monkeypatch.setattr("sys.argv", ["evals", "--model", evals.DEMO, "--case", "no-such-case"])
    with pytest.raises(SystemExit, match="no case 'no-such-case'"):
        evals.main()


def test_the_harness_runs_offline_on_the_demo_model_and_writes_one_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(evals, "_ollama_up", lambda: False)
    out = tmp_path / "report.json"
    argv = ["evals", "--model", "ollama:qwen3", "--suite", "both", "--case", "greeter", "--out", str(out)]
    monkeypatch.setattr("sys.argv", [*argv, "--fallback-to-demo"])
    evals.main()
    said = capsys.readouterr().out
    assert "ollama:qwen3 is not available" in said and "offline demo Copilot" in said
    report = json.loads(out.read_text())
    assert report["model"] == "demo" and report["engine"] == "loop"
    assert [r["id"] for r in report["build"]] == ["greeter"] and report["build"][0]["passed"]  # the demo's greeter
    assert report["debug"] == []  # no debug case is called greeter

    monkeypatch.setattr("sys.argv", argv)  # without the flag a missing model is an error, never a silent demo
    with pytest.raises(SystemExit, match="cannot use ollama:qwen3: Ollama is not running"):
        evals.main()


def test_a_single_suite_keeps_the_list_the_nightly_job_writes(monkeypatch, tmp_path):
    out = tmp_path / "build.json"
    monkeypatch.setattr("sys.argv", ["evals", "--model", "demo", "--case", "greeter", "--out", str(out)])
    evals.main()
    assert isinstance(json.loads(out.read_text()), list)


def test_min_pass_fails_a_suite_below_it(monkeypatch):
    monkeypatch.setattr("sys.argv", ["evals", "--model", "demo", "--case", "triage", "--min-pass", "0.5"])
    with pytest.raises(SystemExit, match=r"build: 0%"):
        evals.main()


def test_the_make_target_is_documented_and_runs_both_suites():
    out = subprocess.run(["make", "-n", "copilot-evals"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert re.search(r"--suite both .*--fallback-to-demo", out) and "ollama:qwen3" in out
    pages = [ROOT / "docs/user-guide/copilot.md", ROOT / "docs/agent-canvas-spec/build-guide/14-copilot-evaluation.md"]
    assert all("make copilot-evals" in p.read_text() for p in pages)


def test_debug_cases_know_their_templates_own_blocks():
    """A template with a block of its own (lead_scoring_pro's templates.score_lead, from its kit) was checked against
    the plain registry, so each of its cases started with "unknown node type" and no fix could ever pass."""
    from agentcanvas.copilot.evals import debug_cases
    from agentcanvas.registry import default_registry
    from agentcanvas.settings import get_settings
    from agentcanvas.validation import validate

    cases = debug_cases(get_settings(), default_registry())
    unknown = [c["id"] for c in cases if any(d.code == "E005" for d in validate(c["start"], c.get("registry")))]
    assert not unknown, unknown
    assert any(c["id"].startswith("lead_scoring_pro/") for c in cases)  # still tested, now fairly
