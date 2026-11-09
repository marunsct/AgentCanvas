/** The trajectory view's rows (G-10): the tree from GET /threads/{id}/trajectory, flattened for display. Pure; unit-tested. */
import type { TrajectoryNode } from "../api/generated";

export interface Row {
  node: TrajectoryNode;
  depth: number;
  open: boolean;
  hasChildren: boolean;
  run: TrajectoryNode; // the run the row belongs to: its duration scales the time bar
}

/** Rows to show: every run, and under each row its children when it is open. Runs, frames and helper agents start
 * open, so the conversation reads at once; steps open when clicked (`toggled` flips a row's default). */
export function rows(runs: TrajectoryNode[], toggled: ReadonlySet<string>): Row[] {
  const out: Row[] = [];
  const walk = (node: TrajectoryNode, depth: number, run: TrajectoryNode) => {
    const children = node.children ?? [];
    const byDefault = node.kind === "run" || node.kind === "group" || node.kind === "agent";
    const open = children.length > 0 && byDefault !== toggled.has(node.id);
    out.push({ node, depth, open, hasChildren: children.length > 0, run });
    if (open) for (const c of children) walk(c, depth + 1, run);
  };
  for (const run of runs) walk(run, 0, run);
  return out;
}

/** Where a row sits on its run's time bar, in percent: {left, width}; null when its time is unknown. */
export function bar(node: TrajectoryNode, run: TrajectoryNode): { left: number; width: number } | null {
  const total = run.duration_ms ?? 0;
  if (node.start_ms == null || !(total > 0)) return null;
  const left = Math.min(100, Math.max(0, (node.start_ms / total) * 100));
  const width = Math.max(1, Math.min(100 - left, ((node.duration_ms ?? 0) / total) * 100));
  return { left, width };
}

/** "850 ms", "1.2 s", "3 min". */
export function took(ms: number | null | undefined): string {
  if (ms == null) return "";
  return ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60_000)} min`;
}

/** Every step a run went through (canvas node ids), to highlight them on the canvas. */
export function stepsOf(node: TrajectoryNode): string[] {
  const out = new Set<string>();
  const walk = (n: TrajectoryNode) => {
    if (n.kind === "step" && n.canvas_node_id) out.add(n.canvas_node_id);
    for (const c of n.children ?? []) walk(c);
  };
  walk(node);
  return [...out];
}
