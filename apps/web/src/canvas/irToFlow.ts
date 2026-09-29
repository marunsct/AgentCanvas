/** IR (+ layout, manifests, diagnostics, run status) -> React Flow nodes and edges. Pure; unit-tested. */
import type { Edge, Node } from "@xyflow/react";
import type { Diagnostic, Layout, NodeManifest } from "../api/types";
import { outputPort, routesOf, type IRNode, type IRResource, type WF } from "../ir/model";
import { formatMetrics, type NodeMetrics, type NodeStatus } from "../run/runStore";

export type ElementKind = "node" | "resource" | "terminal";

export interface CanvasData extends Record<string, unknown> {
  kind: ElementKind;
  label: string;
  name: string;
  type: string;
  manifest?: NodeManifest;
  routes?: string[];
  errors: number;
  warnings: number;
  status: NodeStatus;
  disabled: boolean;
  breakpoint?: boolean;
  highlight?: boolean;
  preview?: string; // tail of the streamed tokens (M4-T3)
  metrics?: string; // "1.2k tok · $0.003 · 850 ms" for the last run (M4-T11)
}

export interface FlowExtras {
  highlight?: string[];
  breakpoints?: string[];
  output?: Record<string, string>;
  metrics?: Record<string, NodeMetrics>;
}

export const PORT_COLORS: Record<string, string> = {
  ChatModel: "#2563eb", Tool: "#6b7280", Middleware: "#db2777", Backend: "#0d9488", Subagent: "#7c3aed",
  Schema: "#ca8a04", Store: "#0891b2", Retriever: "#16a34a", Embeddings: "#9333ea", Flow: "#334155",
};

const COL_W = 260;
const ROW_H = 130;

/** Default positions for elements without a saved layout: columns by distance from START. */
function autoPositions(wf: WF): Layout {
  const depth = new Map<string, number>([["START", 0]]);
  const flow = wf.edges.filter((e) => e.kind === "flow");
  for (let changed = true, guard = 0; changed && guard < wf.nodes.length + 2; guard++) {
    changed = false;
    for (const e of flow) {
      const d = depth.get(e.source.node);
      if (d !== undefined && !depth.has(e.target.node)) {
        depth.set(e.target.node, d + 1);
        changed = true;
      }
    }
  }
  const maxDepth = Math.max(1, ...[...depth.entries()].filter(([k]) => k !== "END").map(([, v]) => v));
  depth.set("END", maxDepth + 1);
  const rows = new Map<number, number>();
  const out: Layout = {};
  const place = (id: string, col: number) => {
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    out[id] = { x: col * COL_W, y: row * ROW_H };
  };
  place("START", 0);
  wf.nodes.forEach((n) => place(n.id, depth.get(n.id) ?? 1));
  place("END", depth.get("END") ?? maxDepth + 1);
  wf.resources.forEach((r, i) => {
    const consumer = wf.edges.find((e) => e.kind === "wiring" && e.source.node === r.id)?.target.node;
    const base = consumer ? out[consumer] : undefined;
    out[r.id] = base ? { x: base.x - 40 + (i % 3) * 30, y: base.y + ROW_H * 1.6 + (i % 2) * 50 } : { x: i * 200, y: ROW_H * 4 };
  });
  return out;
}

export function irToFlow(
  wf: WF,
  layout: Layout,
  manifests: Map<string, NodeManifest>,
  diagnostics: Diagnostic[] = [],
  status: Record<string, NodeStatus> = {},
  selected: string | null = null,
  extras: FlowExtras = {},
): { nodes: Node<CanvasData>[]; edges: Edge[] } {
  const hl = new Set(extras.highlight ?? []);
  const bp = new Set(extras.breakpoints ?? []);
  const auto = autoPositions(wf);
  const placed = (id: string) => layout[id] ?? auto[id] ?? { x: 0, y: 0 };
  // START/END without a saved position sit left/right of whatever the user placed, never on top of it
  const steps = wf.nodes.map((n) => placed(n.id));
  const terminalPos = (id: "START" | "END") => {
    if (layout[id] || steps.length === 0) return placed(id);
    const xs = steps.map((p) => p.x);
    const y = Math.min(...steps.map((p) => p.y));
    return id === "START" ? { x: Math.min(...xs) - 240, y } : { x: Math.max(...xs) + 300, y };
  };
  const pos = (id: string) => (id === "START" || id === "END" ? terminalPos(id) : placed(id));
  const count = (id: string, sev: string) => diagnostics.filter((d) => d.node_id === id && d.severity === sev).length;

  const element = (el: IRNode | IRResource, kind: "node" | "resource"): Node<CanvasData> => {
    const manifest = manifests.get(el.type);
    return {
      id: el.id,
      type: kind === "node" ? "widget" : "resource",
      position: pos(el.id),
      selected: el.id === selected,
      data: {
        kind, manifest, type: el.type, name: el.name, label: el.label || manifest?.label || el.name,
        routes: kind === "node" && manifest?.dynamic_outputs === "routes" ? routesOf(el as IRNode) : undefined,
        errors: count(el.id, "error"), warnings: count(el.id, "warning"), status: status[el.id] ?? "idle",
        disabled: kind === "node" && Boolean((el as IRNode).disabled),
        breakpoint: bp.has(el.id), highlight: hl.has(el.id),
        preview: extras.output?.[el.id] ? extras.output[el.id].slice(-120) : undefined,
        metrics: extras.metrics?.[el.id] ? formatMetrics(extras.metrics[el.id]) || undefined : undefined,
      },
    };
  };
  const terminal = (id: "START" | "END"): Node<CanvasData> => ({
    id, type: "terminal", position: pos(id), deletable: false, selected: false,
    data: { kind: "terminal", label: id, name: id, type: id, errors: 0, warnings: 0, status: "idle", disabled: false },
  });

  const nodes = [terminal("START"), ...wf.nodes.map((n) => element(n, "node")), terminal("END"),
    ...wf.resources.map((r) => element(r, "resource"))];

  const edges: Edge[] = wf.edges.map((e) => {
    if (e.kind === "wiring") {
      const src = wf.resources.find((r) => r.id === e.source.node);
      const out = outputPort(manifests.get(src?.type ?? ""), e.source.port);
      const color = PORT_COLORS[out?.type ?? ""] ?? "#8b5cf6";
      return {
        id: e.id, source: e.source.node, target: e.target.node, sourceHandle: out?.name ?? e.source.port ?? null,
        targetHandle: e.target.port ?? null, className: "edge-wiring", data: { kind: "wiring" },
        style: { stroke: color, strokeDasharray: "5 4", strokeWidth: 1.5 },
      };
    }
    const port = e.source.port ?? undefined;
    const route = port?.startsWith("route:") ? port.slice(6) : port; // route name, or a named output (each/empty)
    const active = status[e.source.node] === "ok" && ["running", "ok", "interrupted"].includes(status[e.target.node] ?? "");
    return {
      id: e.id, source: e.source.node, target: e.target.node, sourceHandle: e.source.port ?? "next", targetHandle: "in",
      label: route, className: "edge-flow", animated: active, data: { kind: e.kind },
      style: { strokeWidth: 2, stroke: active ? "#22c55e" : "#475569" },
    };
  });
  return { nodes, edges };
}
