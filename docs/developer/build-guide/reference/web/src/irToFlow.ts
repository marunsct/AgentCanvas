import { Position, type Edge, type Node } from "@xyflow/react";
import type { Workflow } from "./ir";

export interface CanvasNodeData extends Record<string, unknown> { label: string; kind: string; isResource: boolean }

/** Convert IR -> React Flow nodes/edges. Simple layered layout: column = distance from START.
 *  (Replace with ELK auto-layout in M2; saved positions come from the layout document.) */
export function irToFlow(wf: Workflow): { nodes: Node<CanvasNodeData>[]; edges: Edge[] } {
  const depth = new Map<string, number>([["START", 0]]);
  const flow = wf.edges.filter((e) => e.kind === "flow");
  for (let changed = true, guard = 0; changed && guard < 100; guard++) {
    changed = false;
    for (const e of flow) {
      const d = depth.get(e.source.node);
      if (d !== undefined && (depth.get(e.target.node) ?? -1) < d + 1 && e.target.node !== e.source.node && d < 50) {
        if (!depth.has(e.target.node)) { depth.set(e.target.node, d + 1); changed = true; }
      }
    }
  }
  const perColumn = new Map<number, number>();
  const place = (id: string, col: number) => {
    const row = perColumn.get(col) ?? 0;
    perColumn.set(col, row + 1);
    return { x: col * 240, y: row * 120 };
  };
  const lr = { sourcePosition: Position.Right, targetPosition: Position.Left }; // left-to-right handles
  const nodes: Node<CanvasNodeData>[] = [
    { id: "START", position: place("START", 0), data: { label: "START", kind: "start", isResource: false }, type: "input", ...lr },
    ...wf.nodes.map((n) => ({
      id: n.id, position: place(n.id, depth.get(n.id) ?? 1),
      data: { label: n.label ?? n.name, kind: n.type, isResource: false }, ...lr,
    })),
    { id: "END", position: place("END", Math.max(...depth.values(), 1)), data: { label: "END", kind: "end", isResource: false }, type: "output", ...lr },
    ...wf.resources.map((r, i) => ({
      id: r.id, position: { x: i * 200, y: 420 }, data: { label: r.name, kind: r.type, isResource: true },
    })),
  ];
  const edges: Edge[] = wf.edges.map((e) => ({
    id: e.id, source: e.source.node, target: e.target.node,
    label: e.source.port?.startsWith("route:") ? e.source.port.slice(6) : undefined,
    animated: false,
    style: e.kind === "wiring" ? { strokeDasharray: "4 4", stroke: "#8b5cf6" } : undefined,
  }));
  return { nodes, edges };
}
