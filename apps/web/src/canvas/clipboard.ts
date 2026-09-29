/** Copy/paste of canvas selections (M2-T10): nodes and resources with their internal edges, re-identified on paste. */
import { ulid } from "ulid";
import type { Command } from "../ir/commands";
import { allNames, type IREdge, type IRNode, type IRResource, uniqueName, type WF } from "../ir/model";
import type { Layout } from "../api/types";

export interface Clip {
  nodes: IRNode[];
  resources: IRResource[];
  edges: IREdge[];
  positions: Layout;
}

export function copySelection(wf: WF, ids: string[], layout: Layout): Clip | null {
  const set = new Set(ids);
  const nodes = wf.nodes.filter((n) => set.has(n.id));
  const resources = wf.resources.filter((r) => set.has(r.id));
  if (!nodes.length && !resources.length) return null;
  const edges = wf.edges.filter((e) => set.has(e.source.node) && set.has(e.target.node));
  const positions = Object.fromEntries(ids.filter((id) => layout[id]).map((id) => [id, layout[id]]));
  return structuredClone({ nodes, resources, edges, positions });
}

/** Commands that paste `clip` into `wf` with fresh ids and unique names, plus the new positions. */
export function pasteCommands(wf: WF, clip: Clip, offset = 40): { commands: Command[]; positions: Layout; ids: string[] } {
  const taken = allNames(wf);
  const idMap = new Map<string, string>();
  const commands: Command[] = [];
  for (const n of clip.nodes) {
    const id = `n_${ulid()}`;
    idMap.set(n.id, id);
    const name = uniqueName(n.name, taken);
    taken.push(name);
    commands.push({ op: "AddNode", node: { ...n, id, name } });
  }
  for (const r of clip.resources) {
    const id = `r_${ulid()}`;
    idMap.set(r.id, id);
    const name = uniqueName(r.name, taken);
    taken.push(name);
    commands.push({ op: "AddResource", resource: { ...r, id, name } });
  }
  for (const e of clip.edges) {
    commands.push({
      op: "Connect",
      edge: { ...e, id: `e_${ulid()}`, source: { ...e.source, node: idMap.get(e.source.node)! },
        target: { ...e.target, node: idMap.get(e.target.node)! } },
    });
  }
  const positions: Layout = {};
  for (const [old, pos] of Object.entries(clip.positions)) {
    const id = idMap.get(old);
    if (id) positions[id] = { x: pos.x + offset, y: pos.y + offset };
  }
  return { commands, positions, ids: [...idMap.values()] };
}
