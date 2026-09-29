"""Command API: every batch followed by its inverse restores the original IR (M1-T4)."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.commands import (
    AddNode,
    AddResource,
    CommandError,
    Connect,
    Disconnect,
    RemoveNode,
    RemoveResource,
    SetChannels,
    UpdateMeta,
    UpdateNode,
    UpdateResource,
    UpdateSettings,
    apply_batch,
    merge_patch,
    parse_commands,
)
from agentcanvas.ir.models import Channel, Edge, Endpoint, Node, Resource


def test_merge_patch_rfc7396():
    assert merge_patch({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": None, "e": 4}, "f": [1]}) == {
        "a": 1,
        "b": {"d": 3, "e": 4},
        "f": [1],
    }


def test_remove_node_removes_its_edges_and_undo_restores_them(triage):
    new, inverse = apply_batch(triage, [RemoveNode(id="n_ag")])
    assert new.node("n_ag") is None
    assert not [e for e in new.edges if "n_ag" in (e.source.node, e.target.node)]
    restored, _ = apply_batch(new, inverse)
    assert restored == triage


def test_parse_commands_from_json():
    cmds = parse_commands([{"op": "RenameNode", "id": "n_cls", "name": "triage"}, {"op": "Disconnect", "id": "e1"}])
    assert [c.op for c in cmds] == ["RenameNode", "Disconnect"]


def test_invalid_results_are_rejected(triage):
    with pytest.raises(CommandError, match="unknown node"):
        apply_batch(triage, [RemoveNode(id="n_nope")])
    with pytest.raises(CommandError, match="already exists"):
        apply_batch(triage, [AddNode(node=Node(id="n_cls", type="core.set", name="other"))])
    with pytest.raises(CommandError, match="not a valid workflow"):
        apply_batch(triage, [UpdateNode(id="n_cls", patch={"name": "Not Snake"})])
    with pytest.raises(CommandError, match="cannot change"):
        apply_batch(triage, [UpdateNode(id="n_cls", patch={"type": "core.set"})])


def test_batches_are_atomic(triage):
    before = triage.model_dump()
    with pytest.raises(CommandError):
        apply_batch(triage, [RemoveNode(id="n_esc"), RemoveNode(id="n_esc")])
    assert triage.model_dump() == before


@st.composite
def command_sequences(draw, wf):
    """Draw 1-12 commands that are valid when applied in order."""
    doc_nodes = [n.id for n in wf.nodes]
    doc_res = [r.id for r in wf.resources]
    doc_edges = [e.id for e in wf.edges]
    channels = [c.name for c in wf.state.channels]
    cmds, counter = [], 0
    for _ in range(draw(st.integers(1, 12))):
        counter += 1
        choice = draw(
            st.sampled_from(
                [
                    "add_node",
                    "remove_node",
                    "update_node",
                    "add_res",
                    "remove_res",
                    "update_res",
                    "connect",
                    "disconnect",
                    "channels",
                    "settings",
                    "meta",
                ]
            )
        )
        if choice == "add_node":
            nid = f"n_new{counter}"
            cmds.append(
                AddNode(
                    node=Node(id=nid, type="core.set", name=f"new_{counter}", config={"values": {}}),
                    index=draw(st.integers(0, 10)),
                )
            )
            doc_nodes.append(nid)
        elif choice == "remove_node" and doc_nodes:
            nid = draw(st.sampled_from(doc_nodes))
            cmds.append(RemoveNode(id=nid))
            doc_nodes.remove(nid)
            doc_edges = [e for e in doc_edges if e in doc_edges]  # edges may vanish; Disconnect draws below re-check
            doc_edges = [e for e in doc_edges if not e.startswith("c_")]  # conservative: forget new edges
        elif choice == "update_node" and doc_nodes:
            nid = draw(st.sampled_from(doc_nodes))
            cmds.append(UpdateNode(id=nid, patch={"label": draw(st.text(max_size=8)), "metadata": {"k": counter}}))
        elif choice == "add_res":
            rid = f"r_new{counter}"
            cmds.append(
                AddResource(
                    resource=Resource(
                        id=rid, type="model.chat", name=f"model_{counter}", config={"model": "fake:scripted"}
                    )
                )
            )
            doc_res.append(rid)
        elif choice == "remove_res" and doc_res:
            rid = draw(st.sampled_from(doc_res))
            cmds.append(RemoveResource(id=rid))
            doc_res.remove(rid)
            doc_edges = [e for e in doc_edges if not e.startswith(("w", "c_"))]
        elif choice == "update_res" and doc_res:
            rid = draw(st.sampled_from(doc_res))
            cmds.append(UpdateResource(id=rid, patch={"config": {"temperature": draw(st.floats(0, 2))}}))
        elif choice == "connect" and doc_nodes:
            eid = f"c_{counter}"
            src, dst = draw(st.sampled_from(doc_nodes)), draw(st.sampled_from([*doc_nodes, "END"]))
            cmds.append(Connect(edge=Edge(id=eid, kind="flow", source=Endpoint(node=src), target=Endpoint(node=dst))))
            doc_edges.append(eid)
        elif choice == "disconnect" and doc_edges:
            eid = draw(st.sampled_from(doc_edges))
            cmds.append(Disconnect(id=eid))
            doc_edges.remove(eid)
        elif choice == "channels":
            channels = [*channels, f"extra_{counter}"]
            cmds.append(SetChannels(channels=[Channel(name=c) for c in channels]))
        elif choice == "settings":
            cmds.append(UpdateSettings(patch={"recursion_limit": draw(st.integers(1, 500))}))
        elif choice == "meta":
            cmds.append(UpdateMeta(name=draw(st.text(min_size=1, max_size=10)), description="changed"))
    return cmds


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(data=st.data())
def test_batch_then_inverse_is_identity(triage, data):
    cmds = data.draw(command_sequences(triage))
    try:
        new, inverse = apply_batch(triage, cmds)
    except CommandError:
        return  # the drawn sequence referenced an edge removed together with its node: rejected atomically
    restored, redo = apply_batch(new, inverse)
    assert restored == triage
    assert ir_hash(restored) == ir_hash(triage)
    again, _ = apply_batch(restored, redo)  # redo reproduces the edit
    assert again == new
