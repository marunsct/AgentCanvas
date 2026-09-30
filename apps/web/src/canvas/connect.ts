/** Connection rules (M2-T6): which handles may be wired, and *why not* when refused. */
import type { Connection } from "@xyflow/react";
import type { NodeManifest } from "../api/types";
import { inputPort, outputPort, type WF } from "../ir/model";

export interface Verdict {
  ok: boolean;
  reason?: string;
  kind?: "flow" | "wiring";
}

export function checkConnection(wf: WF, manifests: Map<string, NodeManifest>, c: Connection): Verdict {
  const { source, target, sourceHandle, targetHandle } = c;
  if (!source || !target) return { ok: false, reason: "incomplete connection" };
  if (source === target) return { ok: false, reason: "an element cannot connect to itself" };
  const res = wf.resources.find((r) => r.id === source);
  if (res) {
    const out = outputPort(manifests.get(res.type), sourceHandle);
    const dst = wf.nodes.find((n) => n.id === target) ?? wf.resources.find((r) => r.id === target);
    if (!dst) return { ok: false, reason: "resources connect to widgets, not to START/END" };
    const inp = inputPort(manifests.get(dst.type), targetHandle);
    if (!out) return { ok: false, reason: `${res.name} has no output port` };
    if (!inp) return { ok: false, reason: `connect ${out.type} to one of ${dst.name}'s typed input ports` };
    if (inp.type !== out.type) {
      return { ok: false, reason: `${dst.name}.${inp.name} expects ${inp.type}, but ${res.name} provides ${out.type}` };
    }
    const existing = wf.edges.filter((e) => e.kind === "wiring" && e.target.node === target && e.target.port === inp.name);
    if (existing.some((e) => e.source.node === source)) return { ok: false, reason: "already connected" };
    if (existing.length && !inp.multi) return { ok: false, reason: `${dst.name}.${inp.name} accepts a single ${inp.type}` };
    return { ok: true, kind: "wiring" };
  }
  if (source === "END") return { ok: false, reason: "END has no outgoing flow" };
  if (target === "START") return { ok: false, reason: "nothing can flow into START" };
  if (wf.resources.some((r) => r.id === target)) return { ok: false, reason: "flow edges connect steps, not resources" };
  if (targetHandle && targetHandle !== "in") return { ok: false, reason: "wiring ports accept resources (models, tools…)" };
  const port = flowPort(sourceHandle);
  const dup = wf.edges.some((e) => e.kind === "flow" && e.source.node === source && e.target.node === target &&
    (e.source.port ?? null) === port);
  if (dup) return { ok: false, reason: "already connected" };
  if (port && wf.edges.some((e) => e.kind === "flow" && e.source.node === source && e.source.port === port)) {
    const what = port.startsWith("route:") ? `route '${port.slice(6)}'` : `output '${port}'`;
    return { ok: false, reason: `${what} already has a target` };
  }
  return { ok: true, kind: "flow" };
}

/** IR `source.port` of a flow edge: null for the default "next" output, else the named output (route:x, each…). */
export function flowPort(sourceHandle: string | null | undefined): string | null {
  return sourceHandle && sourceHandle !== "next" ? sourceHandle : null;
}

/** `order` for a new wiring edge into an ordered port (appended after the existing ones). */
export function nextOrder(wf: WF, target: string, port: string | null | undefined): number {
  const orders = wf.edges.filter((e) => e.kind === "wiring" && e.target.node === target && e.target.port === port)
    .map((e, i) => e.order ?? i);
  return orders.length ? Math.max(...orders) + 1 : 0;
}
