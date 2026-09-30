"""Edit commands over the IR (ticket M1-T4; spec doc 02 §4.1).

The canvas, the public API and the Copilot never replace whole documents: they send small,
typed commands. Every command is *pure* (returns a new Workflow) and returns its own
*inverse*, which makes undo/redo, audit and AI edits uniform.

    new_wf, inverse = apply_batch(wf, [AddNode(...), Connect(...)])
    old_wf, _ = apply_batch(new_wf, inverse)          # == wf

Structural invariants (valid names, known edge kinds…) are enforced by the Pydantic models;
semantic problems (unreachable nodes, missing wiring…) are *diagnostics*, because drafts may
be temporarily invalid.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from agentcanvas.ir.models import Channel, ContextField, Edge, Node, Resource, Workflow


class CommandError(ValueError):
    """A command cannot be applied (unknown id, duplicate id, invalid result)."""


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7396 JSON merge patch: dict keys merge recursively, `null` deletes, anything else replaces."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        """Mutate `doc` (a JSON dict of the workflow) in place and return the inverse commands."""
        raise NotImplementedError


def _index(items: list[dict[str, Any]], item_id: str, what: str) -> int:
    for i, it in enumerate(items):
        if it["id"] == item_id:
            return i
    raise CommandError(f"unknown {what} '{item_id}'")


def _ensure_new(doc: dict[str, Any], item_id: str) -> None:
    ids = {x["id"] for key in ("nodes", "resources", "edges") for x in doc[key]}
    if item_id in ids or item_id in ("START", "END"):
        raise CommandError(f"id '{item_id}' already exists")


def _insert(items: list[dict[str, Any]], item: dict[str, Any], index: int | None) -> None:
    items.insert(len(items) if index is None else min(max(index, 0), len(items)), item)


# ------------------------------------------------------------------ nodes ----
class AddNode(_Cmd):
    op: Literal["AddNode"] = "AddNode"
    node: Node
    index: int | None = None

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        _ensure_new(doc, self.node.id)
        _insert(doc["nodes"], self.node.model_dump(mode="json"), self.index)
        return [RemoveNode(id=self.node.id)]


class RemoveNode(_Cmd):
    """Removes a node and every edge touching it."""

    op: Literal["RemoveNode"] = "RemoveNode"
    id: str

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        i = _index(doc["nodes"], self.id, "node")
        node = doc["nodes"].pop(i)
        inverse: list[_Cmd] = [AddNode(node=Node.model_validate(node), index=i)]
        kept = []
        for j, e in enumerate(doc["edges"]):
            if self.id in (e["source"]["node"], e["target"]["node"]):
                inverse.append(Connect(edge=Edge.model_validate(e), index=j))
            else:
                kept.append(e)
        doc["edges"] = kept
        return inverse


class UpdateNode(_Cmd):
    """JSON merge patch on a node (label, config, policies, disabled, metadata, name)."""

    op: Literal["UpdateNode"] = "UpdateNode"
    id: str
    patch: dict[str, Any]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        if "id" in self.patch or "type" in self.patch:
            raise CommandError("UpdateNode cannot change 'id' or 'type'")
        i = _index(doc["nodes"], self.id, "node")
        before = doc["nodes"][i]
        doc["nodes"][i] = merge_patch(before, self.patch)
        return [ReplaceNode(node=Node.model_validate(before))]


class ReplaceNode(_Cmd):
    op: Literal["ReplaceNode"] = "ReplaceNode"
    node: Node

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        i = _index(doc["nodes"], self.node.id, "node")
        before = doc["nodes"][i]
        doc["nodes"][i] = self.node.model_dump(mode="json")
        return [ReplaceNode(node=Node.model_validate(before))]


class RenameNode(_Cmd):
    op: Literal["RenameNode"] = "RenameNode"
    id: str
    name: str

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        return UpdateNode(id=self.id, patch={"name": self.name}).apply(doc)


# -------------------------------------------------------------- resources ----
class AddResource(_Cmd):
    op: Literal["AddResource"] = "AddResource"
    resource: Resource
    index: int | None = None

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        _ensure_new(doc, self.resource.id)
        _insert(doc["resources"], self.resource.model_dump(mode="json"), self.index)
        return [RemoveResource(id=self.resource.id)]


class RemoveResource(_Cmd):
    op: Literal["RemoveResource"] = "RemoveResource"
    id: str

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        i = _index(doc["resources"], self.id, "resource")
        res = doc["resources"].pop(i)
        inverse: list[_Cmd] = [AddResource(resource=Resource.model_validate(res), index=i)]
        kept = []
        for j, e in enumerate(doc["edges"]):
            if self.id in (e["source"]["node"], e["target"]["node"]):
                inverse.append(Connect(edge=Edge.model_validate(e), index=j))
            else:
                kept.append(e)
        doc["edges"] = kept
        return inverse


class UpdateResource(_Cmd):
    op: Literal["UpdateResource"] = "UpdateResource"
    id: str
    patch: dict[str, Any]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        if "id" in self.patch or "type" in self.patch:
            raise CommandError("UpdateResource cannot change 'id' or 'type'")
        i = _index(doc["resources"], self.id, "resource")
        before = doc["resources"][i]
        doc["resources"][i] = merge_patch(before, self.patch)
        return [ReplaceResource(resource=Resource.model_validate(before))]


class ReplaceResource(_Cmd):
    op: Literal["ReplaceResource"] = "ReplaceResource"
    resource: Resource

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        i = _index(doc["resources"], self.resource.id, "resource")
        before = doc["resources"][i]
        doc["resources"][i] = self.resource.model_dump(mode="json")
        return [ReplaceResource(resource=Resource.model_validate(before))]


# ------------------------------------------------------------------ edges ----
class Connect(_Cmd):
    op: Literal["Connect"] = "Connect"
    edge: Edge
    index: int | None = None

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        _ensure_new(doc, self.edge.id)
        _insert(doc["edges"], self.edge.model_dump(mode="json"), self.index)
        return [Disconnect(id=self.edge.id)]


class Disconnect(_Cmd):
    op: Literal["Disconnect"] = "Disconnect"
    id: str

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        i = _index(doc["edges"], self.id, "edge")
        edge = doc["edges"].pop(i)
        return [Connect(edge=Edge.model_validate(edge), index=i)]


# ------------------------------------------------------ state, context, … ----
class SetChannels(_Cmd):
    op: Literal["SetChannels"] = "SetChannels"
    channels: list[Channel]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        before = doc["state"]["channels"]
        doc["state"]["channels"] = [c.model_dump(mode="json") for c in self.channels]
        return [SetChannels(channels=[Channel.model_validate(c) for c in before])]


class SetContext(_Cmd):
    op: Literal["SetContext"] = "SetContext"
    fields: list[ContextField]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        before = doc["context"]["fields"]
        doc["context"]["fields"] = [f.model_dump(mode="json") for f in self.fields]
        return [SetContext(fields=[ContextField.model_validate(f) for f in before])]


class UpdateSettings(_Cmd):
    op: Literal["UpdateSettings"] = "UpdateSettings"
    patch: dict[str, Any]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        before = doc["settings"]
        doc["settings"] = merge_patch(before, self.patch)
        return [ReplaceSettings(settings=before)]


class ReplaceSettings(_Cmd):
    op: Literal["ReplaceSettings"] = "ReplaceSettings"
    settings: dict[str, Any]

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        before = doc["settings"]
        doc["settings"] = self.settings
        return [ReplaceSettings(settings=before)]


class UpdateMeta(_Cmd):
    """Change name / description / metadata of the workflow."""

    op: Literal["UpdateMeta"] = "UpdateMeta"
    name: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None

    def apply(self, doc: dict[str, Any]) -> list[_Cmd]:
        inverse = UpdateMeta(name=doc["name"], description=doc["description"], metadata=doc["metadata"])
        for key in ("name", "description", "metadata"):
            value = getattr(self, key)
            if value is not None:
                doc[key] = value
        return [inverse]


Command = Annotated[
    AddNode
    | RemoveNode
    | UpdateNode
    | ReplaceNode
    | RenameNode
    | AddResource
    | RemoveResource
    | UpdateResource
    | ReplaceResource
    | Connect
    | Disconnect
    | SetChannels
    | SetContext
    | UpdateSettings
    | ReplaceSettings
    | UpdateMeta,
    Field(discriminator="op"),
]
CommandList = TypeAdapter(list[Command])


def parse_commands(raw: list[dict[str, Any]]) -> list[Command]:
    return CommandList.validate_python(raw)


def apply_batch(wf: Workflow, commands: list[Command]) -> tuple[Workflow, list[Command]]:
    """Apply commands atomically. Returns the new workflow and the inverse batch (undo)."""
    doc = wf.model_dump(mode="json")
    inverse: list[_Cmd] = []
    for i, cmd in enumerate(commands):
        try:
            inverse = cmd.apply(doc) + inverse  # inverses run in reverse order
        except CommandError as exc:
            raise CommandError(f"command #{i} ({cmd.op}): {exc}") from exc
    try:
        return Workflow.model_validate(doc), inverse  # type: ignore[return-value]  # every _Cmd is a Command
    except ValidationError as exc:
        raise CommandError(f"the result is not a valid workflow: {exc.errors()[0]['msg']}") from exc
