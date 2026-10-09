from agentcanvas.ir.validate import validate


def codes(wf):
    return sorted(d.code for d in validate(wf))


def test_example_is_valid(triage):
    assert codes(triage) == []


def test_missing_model_wiring(triage):
    triage.edges = [e for e in triage.edges if e.id != "w1"]
    assert "E001" in codes(triage)


def test_router_route_without_edge(triage):
    triage.edges = [e for e in triage.edges if e.id != "e3"]
    assert "E012" in codes(triage)


def test_bad_condition(triage):
    triage.node("n_rt").config["cases"][0]["when"] = "state.urgency =="
    assert "E013" in codes(triage)


def test_unreachable_and_no_end(triage):
    triage.edges = [e for e in triage.edges if e.id not in {"e6", "e7"}]
    assert "E010" in codes(triage)
