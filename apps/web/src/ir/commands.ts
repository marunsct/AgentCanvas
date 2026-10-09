/**
 * Edit commands (mirror of apps/api/agentcanvas/ir/commands.py). The editor applies them locally for
 * instant feedback, then sends them to the server, which is authoritative (validation, inverse, version).
 */
import type * as G from "../api/generated";
import type { WF } from "./model";

/** Generated command types have an optional `op` (it has a default); commands we send always carry it. */
type WithOp<T extends { op?: string }> = Omit<T, "op"> & { op: NonNullable<T["op"]> };

export type Command =
  | WithOp<G.AddNode> | WithOp<G.RemoveNode> | WithOp<G.UpdateNode> | WithOp<G.ReplaceNode> | WithOp<G.RenameNode>
  | WithOp<G.AddResource> | WithOp<G.RemoveResource> | WithOp<G.UpdateResource> | WithOp<G.ReplaceResource>
  | WithOp<G.Connect> | WithOp<G.Disconnect> | WithOp<G.SetChannels> | WithOp<G.SetContext>
  | WithOp<G.UpdateSettings> | WithOp<G.ReplaceSettings> | WithOp<G.UpdateMeta>;

export class CommandError extends Error {}

/** RFC 7396 JSON merge patch. */
export function mergePatch(target: unknown, patch: unknown): unknown {
  if (patch === null || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const result: Record<string, unknown> =
    target && typeof target === "object" && !Array.isArray(target) ? { ...(target as Record<string, unknown>) } : {};
  for (const [k, v] of Object.entries(patch as Record<string, unknown>)) {
    if (v === null) delete result[k];
    else result[k] = mergePatch(result[k], v);
  }
  return result;
}

/**
 * One merge patch with the effect of applying `first` then `second`, keeping the nulls that delete keys; or
 * `undefined` when no single merge patch has that effect (`first` deletes or replaces a value with a scalar and
 * `second` then patches an object into it: merged into the old object, it would resurrect the old keys).
 */
export function composePatches(first: unknown, second: unknown): unknown {
  const isObj = (v: unknown) => v !== null && typeof v === "object" && !Array.isArray(v);
  if (!isObj(second)) return second;
  if (!isObj(first)) return undefined;
  const out: Record<string, unknown> = { ...(first as Record<string, unknown>) };
  for (const [k, v] of Object.entries(second as Record<string, unknown>)) {
    if (!(k in out)) out[k] = v;
    else {
      const c = composePatches(out[k], v);
      if (c === undefined) return undefined;
      out[k] = c;
    }
  }
  return out;
}

function idx<T extends { id: string }>(items: T[], id: string, what: string): number {
  const i = items.findIndex((x) => x.id === id);
  if (i < 0) throw new CommandError(`unknown ${what} '${id}'`);
  return i;
}

function insertAt<T>(items: T[], item: T, index?: number | null): T[] {
  const i = index == null ? items.length : Math.min(Math.max(index, 0), items.length);
  return [...items.slice(0, i), item, ...items.slice(i)];
}

function ensureNew(wf: WF, id: string): void {
  const ids = new Set([...wf.nodes, ...wf.resources, ...wf.edges].map((x) => x.id));
  if (ids.has(id) || id === "START" || id === "END") throw new CommandError(`id '${id}' already exists`);
}

const touches = (e: G.Edge, id: string) => e.source.node === id || e.target.node === id;

export function applyLocal(wf: WF, cmd: Command): WF {
  switch (cmd.op) {
    case "AddNode":
      ensureNew(wf, cmd.node.id);
      return { ...wf, nodes: insertAt(wf.nodes, cmd.node, cmd.index) };
    case "RemoveNode":
      idx(wf.nodes, cmd.id, "node");
      return { ...wf, nodes: wf.nodes.filter((n) => n.id !== cmd.id), edges: wf.edges.filter((e) => !touches(e, cmd.id)) };
    case "UpdateNode":
    case "RenameNode": {
      const i = idx(wf.nodes, cmd.id, "node");
      const patch = cmd.op === "RenameNode" ? { name: cmd.name } : cmd.patch;
      const nodes = [...wf.nodes];
      nodes[i] = mergePatch(nodes[i], patch) as G.Node;
      return { ...wf, nodes };
    }
    case "ReplaceNode": {
      const i = idx(wf.nodes, cmd.node.id, "node");
      const nodes = [...wf.nodes];
      nodes[i] = cmd.node;
      return { ...wf, nodes };
    }
    case "AddResource":
      ensureNew(wf, cmd.resource.id);
      return { ...wf, resources: insertAt(wf.resources, cmd.resource, cmd.index) };
    case "RemoveResource":
      idx(wf.resources, cmd.id, "resource");
      return { ...wf, resources: wf.resources.filter((r) => r.id !== cmd.id), edges: wf.edges.filter((e) => !touches(e, cmd.id)) };
    case "UpdateResource": {
      const i = idx(wf.resources, cmd.id, "resource");
      const resources = [...wf.resources];
      resources[i] = mergePatch(resources[i], cmd.patch) as G.Resource;
      return { ...wf, resources };
    }
    case "ReplaceResource": {
      const i = idx(wf.resources, cmd.resource.id, "resource");
      const resources = [...wf.resources];
      resources[i] = cmd.resource;
      return { ...wf, resources };
    }
    case "Connect":
      ensureNew(wf, cmd.edge.id);
      return { ...wf, edges: insertAt(wf.edges, cmd.edge, cmd.index) };
    case "Disconnect":
      idx(wf.edges, cmd.id, "edge");
      return { ...wf, edges: wf.edges.filter((e) => e.id !== cmd.id) };
    case "SetChannels":
      return { ...wf, state: { channels: cmd.channels } };
    case "SetContext":
      return { ...wf, context: { fields: cmd.fields } };
    case "UpdateSettings":
      return { ...wf, settings: mergePatch(wf.settings, cmd.patch) as WF["settings"] };
    case "ReplaceSettings":
      return { ...wf, settings: cmd.settings as WF["settings"] };
    case "UpdateMeta":
      return {
        ...wf,
        name: cmd.name ?? wf.name,
        description: cmd.description ?? wf.description,
        metadata: (cmd.metadata as Record<string, unknown>) ?? wf.metadata,
        framework: cmd.framework ?? wf.framework,
      };
  }
}

export function applyAll(wf: WF, cmds: Command[]): WF {
  return cmds.reduce(applyLocal, wf);
}
