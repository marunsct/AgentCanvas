/** Auto-layout (M2-T11): ELK layered, left to right, using React Flow's measured node sizes. */
import type { Edge, Node } from "@xyflow/react";
import type { ELK } from "elkjs/lib/elk-api";

let elk: Promise<ELK> | null = null;

/** ELK is large (~1.5 MB): loaded on the first "Tidy", not with the editor. */
function engine(): Promise<ELK> {
  elk ??= import("elkjs/lib/elk.bundled.js").then(({ default: Elk }) => new Elk());
  return elk;
}

export async function tidyLayout(nodes: Node[], edges: Edge[]): Promise<Record<string, { x: number; y: number }>> {
  const res = await (await engine()).layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "40",
      "elk.layered.spacing.nodeNodeBetweenLayers": "90",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: nodes.map((n) => ({ id: n.id, width: n.measured?.width ?? 230, height: n.measured?.height ?? 80 })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  return Object.fromEntries((res.children ?? []).map((c) => [c.id, { x: Math.round(c.x ?? 0), y: Math.round(c.y ?? 0) }]));
}
