import pytest

from agentcanvas.adapters.langchain.nodes import safe_import
from agentcanvas.expr import ExpressionError, eval_condition, render_template
from agentcanvas.ir.validate import validate


@pytest.mark.parametrize(
    "template",
    ["{{ ''.__class__ }}", "{{ state.__class__.__mro__ }}", "{{ cycler.__init__.__globals__ }}"],
)
def test_templates_cannot_reach_python_internals(template):
    with pytest.raises(ExpressionError):
        render_template(template, {"x": 1})


def test_cel_cannot_reach_python_internals():
    with pytest.raises(Exception):  # noqa: B017 - any evaluation error is acceptable, success is not
        eval_condition("state.x.__class__ == 1", {"x": 1})


def test_imports_outside_workspace_are_refused(triage):
    with pytest.raises(PermissionError):
        safe_import("os:system")
    triage.resources[2].config["import_path"] = "os:system"
    assert "E080" in [d.code for d in validate(triage)]


def test_examples_package_is_dev_only(triage, monkeypatch):
    """Demo code under examples.* must not be importable outside dev/test (review v0.6 V-10)."""
    monkeypatch.setenv("AGENTCANVAS_ENV", "prod")
    with pytest.raises(PermissionError):
        safe_import("examples.tools:send_reply")
    assert "E080" in [d.code for d in validate(triage)]
    monkeypatch.setenv("AGENTCANVAS_ENV", "dev")
    assert safe_import("examples.tools:send_reply").name == "send_reply"
