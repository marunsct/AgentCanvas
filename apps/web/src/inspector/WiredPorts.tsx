/** Ordered multi-ports (middleware, tools, subagents): what is wired, in which order, with ↑/↓ to reorder (M3-T12). */
import type { NodeManifest } from "../api/types";
import { useEditor } from "../editor/store";
import type { Command } from "../ir/commands";
import type { IRNode, IRResource, WF } from "../ir/model";

type Edge = WF["edges"][number];

/** Edges wired into `element.port`, in effective order (edge.order, then list position — like the backend). */
export function wiredEdges(wf: WF, element: string, port: string): Edge[] {
  return wf.edges
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => e.kind === "wiring" && e.target.node === element && e.target.port === port)
    .sort((a, b) => (a.e.order ?? Number.MAX_SAFE_INTEGER) - (b.e.order ?? Number.MAX_SAFE_INTEGER) || a.i - b.i)
    .map(({ e }) => e);
}

/** Commands moving edge `from` to position `to`; every edge whose order changes is re-connected with order = index. */
export function reorderCommands(edges: Edge[], from: number, to: number): Command[] {
  if (to < 0 || to >= edges.length || from === to) return [];
  const next = [...edges];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  const changed = next.map((e, order) => ({ e, order })).filter(({ e, order }) => e.order !== order);
  return [
    ...changed.map(({ e }) => ({ op: "Disconnect" as const, id: e.id })),
    ...changed.map(({ e, order }) => ({ op: "Connect" as const, edge: { ...e, order } })),
  ];
}

export function WiredPorts({ element, manifest }: { element: IRNode | IRResource; manifest: NodeManifest }) {
  const wf = useEditor((s) => s.wf);
  const dispatch = useEditor((s) => s.dispatch);
  if (!wf) return null;
  const ports = (manifest.inputs ?? []).filter((p) => p.multi && p.ordered);
  const names = new Map(wf.resources.map((r) => [r.id, r.name]));
  return (
    <>
      {ports.map((p) => {
        const edges = wiredEdges(wf, element.id, p.name);
        if (edges.length < 2) return null;
        return (
          <div key={p.name} className="wired" aria-label={`${p.name} order`}>
            <h4>{p.name} <small className="muted">runs in this order</small></h4>
            <ol>
              {edges.map((e, i) => (
                <li key={e.id}>
                  <code>{names.get(e.source.node) ?? e.source.node}</code>
                  <button type="button" className="link" aria-label={`move ${names.get(e.source.node)} up`} disabled={i === 0}
                    onClick={() => dispatch(reorderCommands(edges, i, i - 1))}>↑</button>
                  <button type="button" className="link" aria-label={`move ${names.get(e.source.node)} down`}
                    disabled={i === edges.length - 1} onClick={() => dispatch(reorderCommands(edges, i, i + 1))}>↓</button>
                </li>
              ))}
            </ol>
          </div>
        );
      })}
    </>
  );
}
