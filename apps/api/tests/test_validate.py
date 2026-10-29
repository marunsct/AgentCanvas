"""Validator rules: one failing and one passing case per rule (ticket M1-T8)."""

import time

import pytest

from agentcanvas.ir.models import Channel, Edge, Endpoint, Node, NodePolicies, Resource, Workflow
from agentcanvas.validation import validate


def codes(wf, **kw):
    return sorted(d.code for d in validate(wf, **kw))


def flow(eid, src, dst, port=None):
    return Edge(id=eid, kind="flow", source=Endpoint(node=src, port=port), target=Endpoint(node=dst))


def wire(eid, src, dst, port, order=None):
    return Edge(id=eid, kind="wiring", source=Endpoint(node=src), target=Endpoint(node=dst, port=port), order=order)


def test_example_is_valid(triage):
    assert codes(triage) == []


def test_missing_model_wiring(triage):
    triage.edges = [e for e in triage.edges if e.id != "w1"]
    assert "E001" in codes(triage)


def test_router_route_without_edge(triage):
    triage.edges = [e for e in triage.edges if e.id != "e3"]
    assert "E012" in codes(triage)


def test_bad_condition(triage):
    triage.require_node("n_rt").config["cases"][0]["when"] = "state.urgency =="
    assert "E013" in codes(triage)


def test_bad_template(triage):
    triage.require_node("n_cls").config["prompt"] = "{{ state.ticket "
    assert "E014" in codes(triage)


def test_unreachable_and_no_end(triage):
    triage.edges = [e for e in triage.edges if e.id not in {"e6", "e7"}]
    assert {"E010", "E015"} <= set(codes(triage))


def test_duplicate_ids_and_names(triage):
    triage.nodes.append(triage.require_node("n_esc").model_copy())
    assert "E000" in codes(triage)


def test_unknown_edge_endpoint(triage):
    triage.edges.append(flow("ex", "n_cls", "n_missing"))
    assert "E003" in codes(triage)


def test_unknown_node_type(triage):
    triage.nodes.append(Node(id="n_x", type="acme.unknown", name="mystery"))
    assert "E005" in codes(triage)


def test_resource_type_used_as_node(triage):
    triage.nodes.append(Node(id="n_x", type="model.chat", name="not_a_node", config={"model": "fake:scripted"}))
    assert "E005" in codes(triage)


def test_config_schema_violation(triage):
    triage.require_node("n_cls").config.pop("output")
    triage.require_node("n_cls").config["temperature"] = 3  # not a property of core.llm
    diags = [d for d in validate(triage) if d.code == "E004"]
    assert len(diags) == 2 and all(d.node_id == "n_cls" for d in diags)


def test_flow_edge_on_resource_is_refused(triage):
    triage.edges.append(flow("ex", "r_fast", "n_cls"))
    assert "E006" in codes(triage)


def test_router_edge_needs_route_port(triage):
    triage.edges.append(flow("ex", "n_rt", "n_esc"))
    assert "E006" in codes(triage)


def test_port_type_mismatch(triage):
    triage.edges.append(wire("wx", "r_send", "n_cls", "model"))  # Tool into ChatModel port
    assert "E002" in codes(triage)


def test_single_port_rejects_second_connection(triage):
    triage.edges.append(wire("wx", "r_fast", "n_ag", "model"))
    assert "E002" in codes(triage)


def test_unknown_input_port(triage):
    triage.edges.append(wire("wx", "r_fast", "n_ag", "brain"))
    assert "E002" in codes(triage)


def test_parallel_writers_to_replace_channel(triage):
    # classify fans out to two set nodes writing the same 'replace' channel in one super-step
    triage.nodes += [
        Node(id="n_a", type="core.set", name="set_a", config={"values": {"summary": "a"}}),
        Node(id="n_b", type="core.set", name="set_b", config={"values": {"summary": "b"}}),
    ]
    triage.edges += [
        flow("x1", "START", "n_a"),
        flow("x2", "START", "n_b"),
        flow("x3", "n_a", "END"),
        flow("x4", "n_b", "END"),
    ]
    assert "E020" in codes(triage)
    triage.state.channel("summary").reducer = "append"  # type: ignore[union-attr]
    assert "E020" not in codes(triage)


def test_writes_and_reads_unknown_channel(triage):
    triage.require_node("n_esc").config["values"] = {"nope": "x"}
    triage.require_node("n_cls").config["prompt"] = "Classify {{ state.missing }}"
    assert {"E021", "E023"} <= set(codes(triage))


def test_an_optional_channel_field_cleared_to_empty_is_not_an_unknown_channel(triage):
    """E-4: clearing an optional output such as a guard's `flag_output` gave "refers to unknown state channel ''"."""
    triage.nodes.append(
        Node(id="n_g", type="guard.pii", name="pii", config={"input": "messages", "flag_output": "", "output": ""})
    )
    triage.edges = [e for e in triage.edges if e.id != "e1"] + [flow("g1", "START", "n_g"), flow("g2", "n_g", "n_cls")]
    assert "E021" not in codes(triage)
    triage.require_node("n_cls").config["output"] = ""  # a required one still is
    assert "E021" in codes(triage)


def test_prose_and_method_calls_are_not_channel_reads(triage):
    """E-4: a prompt saying "state.get is not a field" gave "reads unknown state channel 'get'"."""
    triage.require_node("n_ag").config["system_prompt"] = "Remember: state.get is not a field. Use state.nothing."
    triage.require_node("n_cls").config["prompt"] = "Classify {{ state.get('ticket') }} ({{ state.ticket }})"
    assert "E023" not in codes(triage)
    triage.require_node("n_cls").config["prompt"] = "Classify {{ state.ticket }} {% if state.missing %}!{% endif %}"
    triage.require_node("n_rt").config["cases"][0]["when"] = 'state.ghost == "high"'  # an expression: all of it
    unknown = [d.message for d in validate(triage) if d.code == "E023"]
    assert len(unknown) == 2 and "'missing'" in unknown[0] + unknown[1] and "'ghost'" in unknown[0] + unknown[1]


def test_settings_reference_unknown_channel(triage):
    triage.settings.output_channels = ["urgency", "ghost"]
    assert "E021" in codes(triage)


def test_model_without_tool_calling(triage):
    triage.resource("r_smart").config["model"] = "fake:no-tools"  # type: ignore[union-attr]
    assert "E030" in codes(triage)


def test_duplicate_tool_wiring(triage):
    triage.edges.append(wire("wx", "r_send", "n_ag", "tools"))
    assert "E031" in codes(triage)


def test_approval_for_unwired_tool(triage):
    triage.require_node("n_ag").config["approve_tools"] = ["delete_everything"]
    assert "E033" in codes(triage)


def test_secret_literal(triage):
    triage.resource("r_fast").config["base_url"] = "https://x.example/?key=sk-proj-abcdefghijklmnopqrstuvwx"  # type: ignore[union-attr]
    assert "E050" in codes(triage)


def test_secret_reference_is_fine(triage):
    triage.resource("r_fast").config["api_key"] = {"$secret": "OPENAI_API_KEY"}  # type: ignore[union-attr]
    assert codes(triage) == []


def test_import_path_outside_workspace(triage):
    triage.resource("r_send").config["import_path"] = "os:system"  # type: ignore[union-attr]
    assert "E080" in codes(triage)


def test_local_mcp_needs_admin(triage):
    from agentcanvas.registry import NodeManifest, default_registry

    reg = default_registry().with_manifests(
        [
            NodeManifest(
                type="mcp.server",
                kind="resource",
                label="MCP",
                category="Tools",
                outputs=[{"name": "tools", "type": "Tool"}],
            )
        ]  # type: ignore[list-item]
    )
    triage.resources.append(
        Resource(
            id="r_mcp", type="mcp.server", name="local_fs", config={"transport": "stdio", "command": "./server.py"}
        )
    )
    assert "E042" in codes(triage, registry=reg)
    assert "E042" not in codes(triage, registry=reg, allow_local_mcp=True)


def test_hosted_edition_forbids_host_execution(triage):
    from agentcanvas.registry import NodeManifest, default_registry

    reg = default_registry().with_manifests(
        [
            NodeManifest(
                type="backend.local_shell",
                kind="resource",
                label="Local shell",
                category="Backends",
                outputs=[{"name": "backend", "type": "Backend"}],
                hosted_forbidden=True,
            )
        ]  # type: ignore[list-item]
    )
    triage.resources.append(Resource(id="r_sh", type="backend.local_shell", name="shell"))
    assert "E041" not in codes(triage, registry=reg)
    assert "E041" in codes(triage, registry=reg, edition="hosted")


def test_interrupting_node_with_external_write(triage):
    from agentcanvas.registry import NodeManifest, default_registry

    reg = default_registry().with_manifests(
        [
            NodeManifest(
                type="acme.approve_and_send",
                kind="runtime",
                label="Approve & send",
                category="Custom",
                interrupts=True,
                side_effects=["message_send"],
            )
        ]
    )
    triage.require_node("n_ok").type = "acme.approve_and_send"
    assert "E070" in codes(triage, registry=reg)


def test_loop_without_router(triage):
    triage.edges.append(flow("loop", "n_ok", "n_esc"))
    assert "W001" in codes(triage)


def test_loop_through_router_is_fine(triage):
    triage.edges = [e for e in triage.edges if e.id != "e6"] + [flow("loop", "n_ok", "n_cls")]
    assert "W001" not in codes(triage)


def test_private_channel_written_never_read(triage):
    triage.state.channel("approved").io = "private"  # type: ignore[union-attr]
    assert "W003" in codes(triage)


def test_model_not_in_catalog(triage):
    triage.resource("r_fast").config["model"] = "someprovider:brand-new"  # type: ignore[union-attr]
    assert "W010" in codes(triage)


def test_error_route_must_be_node(triage):
    triage.require_node("n_cls").policies = NodePolicies(error_route="n_nowhere")
    assert "E003" in codes(triage)


def test_delta_storage_requires_batch_safe_reducer():
    with pytest.raises(ValueError, match="ADR 0016"):
        Channel(name="notes", reducer="replace", storage="delta")
    assert Channel(name="messages", reducer="add_messages", storage="delta").storage == "delta"


def test_ir_1_0_documents_are_upgraded(triage):
    doc = triage.model_dump(mode="json")
    doc["ir_version"] = "1.0"
    assert Workflow.model_validate(doc).ir_version == "1.2"


def test_validation_of_200_nodes_is_fast():
    """NFR-01: validation < 150 ms for 200 nodes."""
    nodes, edges, resources = (
        [],
        [],
        [Resource(id="r_m", type="model.chat", name="m", config={"model": "fake:scripted"})],
    )
    prev = "START"
    for i in range(200):
        nid = f"n_{i}"
        if i % 2:
            nodes.append(
                Node(id=nid, type="core.llm", name=f"llm_{i}", config={"prompt": "{{ state.a }}", "output": "b"})
            )
            edges.append(wire(f"w_{i}", "r_m", nid, "model"))
        else:
            nodes.append(Node(id=nid, type="core.set", name=f"set_{i}", config={"values": {"a": "{{ state.b }}"}}))
        edges.append(flow(f"e_{i}", prev, nid))
        prev = nid
    edges.append(flow("e_end", prev, "END"))
    wf = Workflow(
        id="wf_big",
        name="big",
        nodes=nodes,
        edges=edges,
        resources=resources,
        state={"channels": [{"name": "a"}, {"name": "b"}]},
    )  # type: ignore[arg-type]
    validate(wf)  # warm caches (compiled schemas, CEL)
    start = time.perf_counter()
    diags = validate(wf)
    elapsed = time.perf_counter() - start
    assert [d for d in diags if d.severity == "error"] == []
    assert elapsed < 0.15, f"validation took {elapsed * 1000:.0f} ms"


def test_names_and_secrets_that_exports_could_not_use(triage):
    """Milestone review: resources become Python variables and secret names shell variables in exports."""
    triage.resource("r_fast").name = "class"  # type: ignore[union-attr]
    assert "E007" in codes(triage)
    triage.resource("r_fast").name = "builder"  # type: ignore[union-attr]
    assert "E007" in codes(triage)
    triage.resource("r_fast").name = "fast"  # type: ignore[union-attr]
    assert "E007" not in codes(triage)
    triage.resource("r_fast").config["api_key"] = {"$secret": "K$(touch /tmp/p)"}  # type: ignore[union-attr]
    assert "E051" in codes(triage)
    triage.resource("r_fast").config["api_key"] = {"$secret": "OPENAI_API_KEY"}  # type: ignore[union-attr]
    assert "E051" not in codes(triage)


def test_hosted_edition_keeps_tools_off_private_networks(triage):
    triage.resources.append(
        Resource(
            id="r_web",
            type="tool.http",
            name="web",
            config={"url": "https://x.example", "allowed_domains": ["x.example"], "allow_private_network": True},
        )
    )
    assert "E043" in codes(triage, edition="hosted")
    assert "E043" not in codes(triage)
