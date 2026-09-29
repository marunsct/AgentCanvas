/** Node palette (M2-T4): categories from the registry, search, drag onto the canvas (or click to add). */
import { useMemo, useState } from "react";
import { useNodeTypes } from "../api/queries";
import type { NodeManifest } from "../api/types";
import { addElement, DND_MIME, manifestCache } from "./Canvas";

export function Palette() {
  const { data, isLoading } = useNodeTypes();
  const [q, setQ] = useState("");
  for (const m of data ?? []) manifestCache.set(m.type, m);

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hit = (m: NodeManifest) =>
      !needle || `${m.label} ${m.type} ${m.description ?? ""} ${m.category}`.toLowerCase().includes(needle);
    const out = new Map<string, NodeManifest[]>();
    for (const m of data ?? []) if (hit(m)) out.set(m.category, [...(out.get(m.category) ?? []), m]);
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data, q]);

  return (
    <aside className="palette" aria-label="Node palette">
      <input className="search" placeholder="Search widgets…" value={q} onChange={(e) => setQ(e.target.value)}
        aria-label="Search widgets" />
      {isLoading && <p className="muted">Loading…</p>}
      {groups.map(([cat, items]) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {items.map((m) => (
            <div
              key={m.type} className={`palette-item kind-${m.kind}`} draggable role="button" tabIndex={0}
              title={m.description ?? m.type} data-testid={`palette-${m.type}`}
              style={{ borderLeftColor: m.color ?? "#64748b" }}
              onDragStart={(e) => { e.dataTransfer.setData(DND_MIME, m.type); e.dataTransfer.effectAllowed = "move"; }}
              onDoubleClick={() => addElement(m.type)}
              onKeyDown={(e) => { if (e.key === "Enter") addElement(m.type); }}
            >
              <b>{m.label}</b>
              <small>{m.kind === "resource" ? "resource" : m.type}</small>
            </div>
          ))}
        </section>
      ))}
    </aside>
  );
}
