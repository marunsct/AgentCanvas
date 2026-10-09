/** Which installed framework can run which blocks (S20). Mirrors `registry/adapters.py`: the manifests say
 * `native`, `emulated` or `unsupported` per block type, with `"<namespace>.*"` and `"*"` as fallbacks. Pure. */
import type { AdapterManifest } from "../api/types";
import type { WF } from "./model";

export type Support = "native" | "emulated" | "unsupported";

/** AI blocks a framework can design differently: the only ones that may name an `engine` (IR 1.2). */
export const ENGINE_TYPES = new Set(["core.llm", "core.agent", "agent.deep"]);

export function support(m: Pick<AdapterManifest, "nodes">, type: string): Support {
  const nodes = (m.nodes ?? {}) as Record<string, Support>;
  if (type in nodes) return nodes[type];
  const ns = `${type.split(".", 1)[0]}.*`;
  return nodes[ns] ?? nodes["*"] ?? "unsupported";
}

export interface Fit {
  id: string;
  label: string;
  ok: boolean;
  blockers: { id: string; name: string; type: string }[];
  emulated: { id: string; name: string; type: string }[];
}

export function portability(wf: Pick<WF, "nodes" | "resources">, frameworks: AdapterManifest[]): Fit[] {
  return frameworks.map((m) => {
    const blockers: Fit["blockers"] = [];
    const emulated: Fit["emulated"] = [];
    for (const el of [...wf.nodes, ...wf.resources]) {
      const level = support(m, el.type);
      const entry = { id: el.id, name: el.name, type: el.type };
      if (level === "unsupported") blockers.push(entry);
      else if (level === "emulated") emulated.push(entry);
    }
    return { id: m.id, label: m.label, ok: blockers.length === 0, blockers, emulated };
  });
}
