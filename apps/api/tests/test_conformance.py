"""The conformance kit (S20) on every adapter: the same workflows behave the same on each framework."""

from __future__ import annotations

import pytest

from agentcanvas.adapters import KNOWN, create_adapter
from agentcanvas.conformance import CASES, applicable, run_case
from agentcanvas.registry.adapters import all_manifests


@pytest.fixture(params=KNOWN)
async def adapter(request):
    a = create_adapter(request.param)
    async with a.persistence(None):
        yield a


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_every_adapter_passes_the_kit(adapter, case):
    if not applicable(adapter.manifest, case):
        pytest.skip(f"{adapter.id} does not run {sorted(case.types())}")
    result = await run_case(adapter, case, adapter.default_resources())
    assert result.passed, "\n".join(result.problems)


def test_every_adapter_runs_every_case():
    """No adapter may skip the kit: each one shipped declares support for every block the cases use."""
    for adapter_id in KNOWN:
        m = all_manifests()[adapter_id]
        assert [c.id for c in CASES if not applicable(m, c)] == []
