/** "What can connect here?" (click a port): the block types and existing elements a port can be wired to. Pure. */
import type { NodeManifest } from "../api/types";
import type { Command } from "../ir/commands";
import type { IREdge, WF } from "../ir/model";
import { categoryRank, firstSentence, typeName } from "../help/glossary";
import { checkConnection, flowPort, nextOrder } from "./connect";

/** The port that was clicked: a block's output (`side: "out"`) or one of its typed inputs (`side: "in"`). */
export interface PortRef {
  node: string; // element id, or START
  handle: string; // React Flow handle id: next, route:x, each, model, tools…
  side: "out" | "in";
}

export interface NewChoice {
  type: string;
  label: string;
  blurb: string;
  category: string;
  port: string | null; // the new element's port that gets connected (null: its flow input)
}

export interface ExistingChoice {
  id: string;
  name: string;
  port: string | null;
}

export interface Suggestions {
  title: string;
  intro: string;
  add: NewChoice[];
  existing: ExistingChoice[];
}

/** Steps a beginner usually wants next, listed first. */
const FAVOURITES = ["core.llm", "core.agent", "core.approval", "core.router", "core.human_input", "core.set"];

const rank = (m: NodeManifest) => {
  const fav = FAVOURITES.indexOf(m.type);
  return [fav === -1 ? FAVOURITES.length : fav, categoryRank(m.category), m.label] as const;
};
const byRank = (a: NodeManifest, b: NodeManifest) => {
  const [fa, ca, la] = rank(a);
  const [fb, cb, lb] = rank(b);
  return fa - fb || ca - cb || la.localeCompare(lb);
};
const choice = (m: NodeManifest, port: string | null): NewChoice =>
  ({ type: m.type, label: m.label, blurb: firstSentence(m.description), category: m.category, port });

export function suggest(wf: WF, manifests: Map<string, NodeManifest>, at: PortRef): Suggestions | null {
  const all = [...manifests.values()];
  const res = wf.resources.find((r) => r.id === at.node);
  const node = wf.nodes.find((n) => n.id === at.node);
  const self = res ?? node;

  if (at.side === "out" && !res) {
    // a flow output (a step, or START): any step can follow, or Finish
    const ok = (target: string) =>
      checkConnection(wf, manifests, { source: at.node, sourceHandle: at.handle, target, targetHandle: "in" }).ok;
    const port = flowPort(at.handle); // named outputs (routes, each…) lead to one step; "next" may fan out
    if (port && wf.edges.some((e) => e.kind === "flow" && e.source.node === at.node && e.source.port === port)) {
      return {
        title: "This output is already connected",
        intro: "Each output leads to one next step. Delete its arrow first (click it, press Delete) to choose another.",
        add: [], existing: [],
      };
    }
    const route = at.handle.startsWith("route:") ? ` on the “${at.handle.slice(6)}” path` : "";
    return {
      title: "What can come next?",
      intro: `Pick the step that runs after ${at.node === "START" ? "the start" : self?.name ?? "this one"}${route}. Click one to add it, already connected.`,
      add: all.filter((m) => m.kind === "runtime").sort(byRank).map((m) => choice(m, null)),
      existing: [
        ...(ok("END") ? [{ id: "END", name: "Finish (END)", port: null }] : []),
        ...wf.nodes.filter((n) => n.id !== at.node && ok(n.id)).map((n) => ({ id: n.id, name: n.label || n.name, port: null })),
      ],
    };
  }

  if (at.side === "out" && res) {
    // a resource's output: the blocks that take its type
    const type = manifests.get(res.type)?.outputs?.find((p) => p.name === at.handle)?.type
      ?? manifests.get(res.type)?.outputs?.[0]?.type;
    if (!type) return null;
    const takers = all.filter((m) => m.type !== res.type && (m.inputs ?? []).some((p) => p.type === type)).sort(byRank);
    const existing: ExistingChoice[] = [];
    for (const el of [...wf.nodes, ...wf.resources]) {
      if (el.id === res.id) continue;
      for (const p of manifests.get(el.type)?.inputs ?? []) {
        if (p.type !== type) continue;
        const v = checkConnection(wf, manifests, { source: res.id, sourceHandle: at.handle, target: el.id, targetHandle: p.name });
        if (v.ok) existing.push({ id: el.id, name: `${el.label || el.name} → ${p.name}`, port: p.name });
      }
    }
    return {
      title: `Where can this ${typeName(type)} plug in?`,
      intro: `A ${typeName(type)} is not a step: it plugs into a block's “${takers[0]?.inputs?.find((p) => p.type === type)?.name ?? type}” input. Click to add a block that uses it, already wired.`,
      add: takers.map((m) => choice(m, m.inputs!.find((p) => p.type === type)!.name)),
      existing,
    };
  }

  // side "in": a typed input of a step or resource (model, tools, middleware…)
  const port = self ? manifests.get(self.type)?.inputs?.find((p) => p.name === at.handle) : undefined;
  if (!self || !port) return null;
  const givers = all.filter((m) => m.kind === "resource" && (m.outputs ?? []).some((p) => p.type === port.type)).sort(byRank);
  const existing = wf.resources
    .filter((r) => r.id !== self.id)
    .map((r) => ({ r, out: manifests.get(r.type)?.outputs?.find((p) => p.type === port.type) }))
    .filter(({ r, out }) => out && checkConnection(wf, manifests,
      { source: r.id, sourceHandle: out.name, target: self.id, targetHandle: port.name }).ok)
    .map(({ r, out }) => ({ id: r.id, name: r.name, port: out!.name }));
  return {
    title: `What plugs into “${port.name}”?`,
    intro: `${port.description ?? `This input takes a ${typeName(port.type)}.`} Click one to add it, already wired.`,
    add: givers.map((m) => choice(m, (m.outputs ?? []).find((p) => p.type === port.type)!.name)),
    existing,
  };
}

/** The edge that links the clicked port to element `other` (through `otherPort` on that element). */
export function linkEdge(wf: WF, at: PortRef, other: string, otherPort: string | null, id: string): IREdge {
  if (at.side === "in") {
    return {
      id, kind: "wiring", source: { node: other, port: otherPort }, target: { node: at.node, port: at.handle },
      order: nextOrder(wf, at.node, at.handle),
    };
  }
  const isRes = wf.resources.some((r) => r.id === at.node);
  if (isRes) {
    return {
      id, kind: "wiring", source: { node: at.node, port: at.handle }, target: { node: other, port: otherPort },
      order: nextOrder(wf, other, otherPort),
    };
  }
  return { id, kind: "flow", source: { node: at.node, port: flowPort(at.handle) }, target: { node: other, port: null } };
}

/** Connect the clicked port to an element that already exists. */
export function connectExisting(wf: WF, at: PortRef, choice: ExistingChoice, id: string): Command[] {
  return [{ op: "Connect", edge: linkEdge(wf, at, choice.id, choice.port, id) }];
}
