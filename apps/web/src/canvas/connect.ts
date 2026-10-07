/** Connection rules (M2-T6): which handles may be wired, and *why not* when refused. */
import type { Connection } from "@xyflow/react";
import type { NodeManifest } from "../api/types";
import { inputPort, outputPort, type WF } from "../ir/model";
import { aType, typeName } from "../help/glossary";

export interface Verdict {
  ok: boolean;
  reason?: string;
  kind?: "flow" | "wiring";
}

export function checkConnection(wf: WF, manifests: Map<string, NodeManifest>, c: Connection): Verdict {
  const { source, target, sourceHandle, targetHandle } = c;
  if (!source || !target) return { ok: false, reason: "drop the arrow on a dot of another block" };
  if (source === target) return { ok: false, reason: "a block cannot connect to itself" };
  const res = wf.resources.find((r) => r.id === source);
  if (res) {
    const out = outputPort(manifests.get(res.type), sourceHandle);
    const dst = wf.nodes.find((n) => n.id === target) ?? wf.resources.find((r) => r.id === target);
    if (!dst) return { ok: false, reason: `${res.name} plugs into a step's coloured input, not into Start or Finish` };
    const inp = inputPort(manifests.get(dst.type), targetHandle);
    if (!out) return { ok: false, reason: `${res.name} has nothing to plug in` };
    const fits = (manifests.get(dst.type)?.inputs ?? []).filter((p) => p.type === out.type).map((p) => `“${p.name}”`);
    if (!inp) {
      return { ok: false, reason: fits.length
        ? `${res.name} is ${aType(out.type)}: drop it on ${dst.name}'s ${fits.join(" or ")} input`
        : `${dst.name} has no input for ${aType(out.type)}` };
    }
    if (inp.type !== out.type) {
      return { ok: false, reason: `${dst.name}'s “${inp.name}” input expects ${aType(inp.type)} (${inp.type}), but ${res.name} is ${aType(out.type)} (${out.type})${fits.length ? `; try ${fits.join(" or ")}` : ""}` };
    }
    const existing = wf.edges.filter((e) => e.kind === "wiring" && e.target.node === target && e.target.port === inp.name);
    if (existing.some((e) => e.source.node === source)) return { ok: false, reason: "these two are already connected" };
    if (existing.length && !inp.multi) {
      return { ok: false, reason: `${dst.name}'s “${inp.name}” takes a single ${typeName(inp.type)} (${inp.type}); delete the existing arrow first` };
    }
    return { ok: true, kind: "wiring" };
  }
  if (source === "END") return { ok: false, reason: "Finish is the end: nothing comes after it" };
  if (target === "START") return { ok: false, reason: "Start is the beginning: nothing can lead into it" };
  if (wf.resources.some((r) => r.id === target)) {
    return { ok: false, reason: "that is an add-on, not a step: drag from its right-hand dot into a step's coloured input instead" };
  }
  if (targetHandle && targetHandle !== "in") {
    return { ok: false, reason: "coloured inputs take add-ons such as an AI model; drop the arrow on the step's top-left dot instead" };
  }
  const port = flowPort(sourceHandle);
  const dup = wf.edges.some((e) => e.kind === "flow" && e.source.node === source && e.target.node === target &&
    (e.source.port ?? null) === port);
  if (dup) return { ok: false, reason: "these two are already connected" };
  if (port && wf.edges.some((e) => e.kind === "flow" && e.source.node === source && e.source.port === port)) {
    const what = port.startsWith("route:") ? `path '${port.slice(6)}'` : `output '${port}'`;
    return { ok: false, reason: `${what} already has a target; delete its arrow first to change it` };
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
