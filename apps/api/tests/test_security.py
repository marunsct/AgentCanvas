import time

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


LOOPS = "{% set n = namespace(i=0) %}{% for a in range(100000) %}{% for b in range(1000) %}{% set n.i = n.i + 1 %}"
EMPTY_LOOPS = "{% for a in state.x %}{% for b in state.x %}{% for c in state.x %}{% endfor %}{% endfor %}{% endfor %}"
RECURSION = "{% macro f(n) %}{% if n %}{{ f(n-1) }}{{ f(n-1) }}{% endif %}{% endmacro %}{{ f(40) }}"


@pytest.mark.parametrize(
    "template",
    [
        LOOPS + "{% endfor %}{% endfor %}",
        EMPTY_LOOPS,
        RECURSION,
        "{{ 'x' * 10**10 }}",
        "{{ 'x' * 1000 * 1000 * 1000 }}",
        "{{ 9 ** (9 ** 9) }}",
        "{{ 'x'|center(10**10) }}",
        "{{ 'x'.rjust(10**10) }}",
        "{{ '%10000000000s' % 'x' }}",
        "{{ '{:>{}}'.format('x', 10**10) }}",
        "{{ range(100000)|join('x' * 1000000) }}",
        "{{ [0]|batch(10**10, 0)|list }}",
        "{% set n = namespace(s='x') %}{% for i in range(60) %}{% set n.s = n.s ~ n.s %}{% endfor %}",
        "{% for i in range(100000) %}{{ 'x' * 1000 }}{% endfor %}",
        "{{ {'a': 1}|tojson(indent=10**9) }}",
        "{{ ('x' * 1000000)|replace('', 'y' * 100) }}",
        "{% set x = [1] %}{% for i in range(60) %}{{ x.extend(x) }}{% endfor %}",
    ],
)
def test_templates_cannot_run_for_long_or_build_huge_values(template):
    """A template renders on the server's event loop: one that loops for hours or builds gigabytes is stopped (within
    about a second, without allocating) instead of freezing or killing the server for everyone."""
    started = time.monotonic()
    with pytest.raises(ExpressionError, match=r"took longer|too big"):
        render_template(template, {"x": list(range(5000))})
    assert time.monotonic() - started < 3


def test_bounded_templates_still_render_ordinary_ones():
    state = {"x": list(range(10)), "name": "Ann"}
    for template, out in [
        ("{% for x in state.x[:3] %}{{ loop.index }}/{{ loop.length }}:{{ x }} {% endfor %}", "1/3:0 2/3:1 3/3:2 "),
        ("{% for x in [] %}a{% else %}none{% endfor %}", "none"),
        ("{{ state.x[:3]|join(', ') }}", "0, 1, 2"),
        ("{{ '%05d' % 42 }} {{ 7 % 3 }} {{ '{:>4}'.format('a') }}", "00042 1    a"),
        ("{{ 'a' ~ 1 ~ state.name }}", "a1Ann"),
        ("{{ [[1], [2]]|sum(start=[]) }} {{ 1731000000 * 1000 }} {{ 2 ** 10 }}", "[1, 2] 1731000000000 1024"),
        ("{{ 'a-b'|replace('-', '+') }} {{ 'x'|center(3) }} {{ [1, 2, 3]|batch(2)|list }}", "a+b  x  [[1, 2], [3]]"),
        ("{% for k, v in {'a': 1}.items() %}{{ k }}={{ v }}{% endfor %}{{ 'a\\nb'|indent(2, true) }}", "a=1  a\n  b"),
        ("{{ 'x' * 100000 }}", "x" * 100000),
    ]:
        assert render_template(template, state) == out


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
