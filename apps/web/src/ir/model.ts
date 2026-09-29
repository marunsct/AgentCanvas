/** IR helpers for the editor. Types come from the generated JSON-Schema types (`pnpm gen:ir`). */
import type * as G from "./generated";
import type { NodeManifest, PortSpec } from "../api/types";

export type Channel = G.Channel;
export type ContextField = G.ContextField;
export type IRNode = G.Node;
export type IRResource = G.Resource;
export type IREdge = G.Edge;
export type NodePolicies = G.NodePolicies;
export type Settings = G.WorkflowSettings;

/** A workflow with every collection present (the server always sends full documents). */
export interface WF {
  ir_version: "1.0" | "1.1";
  id: string;
  name: string;
  description: string;
  kind: "workflow" | "component";
  framework: string;
  state: { channels: Channel[] };
  context: { fields: ContextField[] };
  resources: IRResource[];
  nodes: IRNode[];
  edges: IREdge[];
  settings: Settings;
  metadata: Record<string, unknown>;
}

export function normalize(w: G.Workflow): WF {
  return {
    ir_version: w.ir_version ?? "1.1",
    id: w.id,
    name: w.name,
    description: w.description ?? "",
    kind: w.kind ?? "workflow",
    framework: w.framework ?? "langchain",
    state: { channels: w.state?.channels ?? [] },
    context: { fields: w.context?.fields ?? [] },
    resources: w.resources ?? [],
    nodes: w.nodes ?? [],
    edges: w.edges ?? [],
    settings: w.settings ?? {},
    metadata: (w.metadata as Record<string, unknown>) ?? {},
  };
}

export const SPECIAL = new Set(["START", "END"]);

export function findElement(wf: WF, id: string): IRNode | IRResource | undefined {
  return wf.nodes.find((n) => n.id === id) ?? wf.resources.find((r) => r.id === id);
}

export function isResource(wf: WF, id: string): boolean {
  return wf.resources.some((r) => r.id === id);
}

/** Router route names: one output handle per case + default. */
export function routesOf(node: IRNode): string[] {
  const cfg = node.config ?? {};
  const cases = Array.isArray(cfg.cases) ? (cfg.cases as { route?: unknown }[]) : [];
  const routes = cases.map((c) => (typeof c.route === "string" ? c.route : "")).filter(Boolean);
  const dflt = typeof cfg.default === "string" ? cfg.default : "default";
  return [...new Set([...routes, dflt])];
}

/** snake_case identifier from a label, unique among existing names. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  let slug = base.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  if (!/^[a-z]/.test(slug)) slug = `n_${slug}`;
  slug = slug.slice(0, 50) || "node";
  const used = new Set(taken);
  if (!used.has(slug) && !SPECIAL.has(slug.toUpperCase())) return slug;
  for (let i = 2; ; i++) if (!used.has(`${slug}_${i}`)) return `${slug}_${i}`;
}

export function allNames(wf: WF): string[] {
  return [...wf.nodes.map((n) => n.name), ...wf.resources.map((r) => r.name)];
}

/** Default config from a JSON Schema: `default` values of top-level properties. */
export function defaultsFromSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const props = (schema.properties ?? {}) as Record<string, { default?: unknown }>;
  return Object.fromEntries(Object.entries(props).filter(([, p]) => p.default !== undefined).map(([k, p]) => [k, p.default]));
}

export function inputPort(m: NodeManifest | undefined, name: string | null | undefined): PortSpec | undefined {
  return m?.inputs?.find((p) => p.name === name);
}

export function outputPort(m: NodeManifest | undefined, name: string | null | undefined): PortSpec | undefined {
  const outs = m?.outputs ?? [];
  return name ? outs.find((p) => p.name === name) : outs.length === 1 ? outs[0] : undefined;
}

/** The workflow embedded in a chain of `core.subgraph` nodes (drill-in view, M2-T13); null if the path is stale. */
export function innerWorkflow(wf: WF, path: string[]): WF | null {
  let cur: WF = wf;
  for (const id of path) {
    const n = cur.nodes.find((x) => x.id === id);
    const inner = (n?.config as { workflow?: G.Workflow } | undefined)?.workflow;
    if (!n || n.type !== "core.subgraph" || !inner) return null;
    cur = normalize({ ...inner, id: inner.id ?? `${cur.id}.${n.name}`, name: inner.name ?? n.label ?? n.name });
  }
  return cur;
}
