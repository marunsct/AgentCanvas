/** Reviewing a Copilot proposal change by change (FR-COP-01): what each command does in words, which commands need
 * which (a wire needs the step it connects), and the canvas preview of the ones ticked, removed blocks included. */
import { applyLocal, type Command } from "../ir/commands";
import type { WF } from "../ir/model";

export interface ReviewDiff { added: string[]; changed: string[]; removed: string[] }

const nameOf = (wf: WF, id: string) => {
  const el = [...wf.nodes, ...wf.resources].find((x) => x.id === id);
  return el ? `“${el.label || el.name}”` : id === "START" ? "Start" : id === "END" ? "Finish" : "a step";
};

/** The command in the person's words, read against the workflow it applies to (and the commands before it). */
export function describe(cmd: Command, wf: WF): string {
  switch (cmd.op) {
    case "AddNode": return `Add the step “${cmd.node.label || cmd.node.name}”`;
    case "RemoveNode": return `Remove the step ${nameOf(wf, cmd.id)}`;
    case "UpdateNode": return `Change the settings of ${nameOf(wf, cmd.id)}`;
    case "ReplaceNode": return `Change the settings of ${nameOf(wf, cmd.node.id)}`;
    case "RenameNode": return `Rename ${nameOf(wf, cmd.id)} to “${cmd.name}”`;
    case "AddResource": return `Add “${cmd.resource.label || cmd.resource.name}”`;
    case "RemoveResource": return `Remove ${nameOf(wf, cmd.id)}`;
    case "UpdateResource": return `Change the settings of ${nameOf(wf, cmd.id)}`;
    case "ReplaceResource": return `Change the settings of ${nameOf(wf, cmd.resource.id)}`;
    case "Connect": return `Connect ${nameOf(wf, cmd.edge.source.node)} to ${nameOf(wf, cmd.edge.target.node)}`;
    case "Disconnect": {
      const e = wf.edges.find((x) => x.id === cmd.id);
      return e ? `Disconnect ${nameOf(wf, e.source.node)} from ${nameOf(wf, e.target.node)}` : "Remove a connection";
    }
    case "SetChannels": return "Change the workflow's fields";
    case "SetContext": return "Change the run context";
    case "UpdateSettings": case "ReplaceSettings": return "Change the workflow's settings";
    case "UpdateMeta": return "Change the layout";
  }
}

function creates(cmd: Command): string[] {
  if (cmd.op === "AddNode") return [cmd.node.id];
  if (cmd.op === "AddResource") return [cmd.resource.id];
  if (cmd.op === "Connect") return [cmd.edge.id];
  return [];
}

function uses(cmd: Command): string[] {
  if (cmd.op === "Connect") return [cmd.edge.source.node, cmd.edge.target.node];
  if (cmd.op === "ReplaceNode") return [cmd.node.id];
  if (cmd.op === "ReplaceResource") return [cmd.resource.id];
  if ("id" in cmd && typeof cmd.id === "string") return [cmd.id];
  return [];
}

/** For each command, the earlier commands it needs (the ones that create what it refers to). */
export function dependencies(cmds: Command[]): number[][] {
  const madeBy = new Map<string, number>();
  return cmds.map((cmd, i) => {
    const needs = [...new Set(uses(cmd).map((id) => madeBy.get(id)).filter((j): j is number => j !== undefined))];
    for (const id of creates(cmd)) madeBy.set(id, i);
    return needs;
  });
}

/** Tick or untick command `i`: ticking also ticks what it needs; unticking also unticks what needs it. */
export function toggle(selected: Set<number>, i: number, deps: number[][]): Set<number> {
  const next = new Set(selected);
  if (next.has(i)) {
    const drop = (j: number) => {
      next.delete(j);
      deps.forEach((d, k) => { if (d.includes(j) && next.has(k)) drop(k); });
    };
    drop(i);
  } else {
    const add = (j: number) => { next.add(j); deps[j].forEach((d) => { if (!next.has(d)) add(d); }); };
    add(i);
  }
  return next;
}

/** The workflow as the ticked commands would leave it, with what they remove kept (and marked) so the canvas can
 * show it; and the ids each kind of change touches. Commands that no longer apply are skipped. */
export function preview(before: WF, cmds: Command[], selected: Set<number>): { workflow: WF; diff: ReviewDiff } {
  let after = before;
  cmds.forEach((cmd, i) => {
    if (!selected.has(i)) return;
    try { after = applyLocal(after, cmd); } catch { /* stale: the server refuses it on accept */ }
  });
  const key = (x: unknown) => JSON.stringify(x);
  const items = (wf: WF) => new Map([...wf.nodes, ...wf.resources, ...wf.edges].map((x) => [x.id, key(x)]));
  const a = items(before), b = items(after);
  const diff: ReviewDiff = {
    added: [...b.keys()].filter((id) => !a.has(id)),
    changed: [...b.keys()].filter((id) => a.has(id) && a.get(id) !== b.get(id)),
    removed: [...a.keys()].filter((id) => !b.has(id)),
  };
  const gone = new Set(diff.removed);
  const workflow: WF = {
    ...after,
    nodes: [...after.nodes, ...before.nodes.filter((n) => gone.has(n.id))],
    resources: [...after.resources, ...before.resources.filter((r) => gone.has(r.id))],
    edges: [...after.edges, ...before.edges.filter((e) => gone.has(e.id))],
  };
  return { workflow, diff };
}
