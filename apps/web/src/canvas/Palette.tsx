/** Node palette (M2-T4): categories from the registry, search, drag onto the canvas (or click to add). */
import { useMemo, useState } from "react";
import { useNodeTypes, useSetup, useWorkspaceCatalog } from "../api/queries";
import type { NodeManifest } from "../api/types";
import { addElement, DND_MIME, manifestCache, type Preset, projectModel } from "./Canvas";
import { QueryStatus } from "../ui/QueryStatus";
import { categoryRank, firstSentence } from "../help/glossary";

interface WorkspaceItem {
  key: string;
  type: string;
  label: string;
  hint: string;
  preset: Preset;
}

export function Palette() {
  const nodeTypes = useNodeTypes();
  const { data } = nodeTypes;
  const catalog = useWorkspaceCatalog();
  const [q, setQ] = useState("");
  for (const m of data ?? []) manifestCache.set(m.type, m);
  const dm = useSetup().data?.default_model;
  projectModel.current = dm ? Object.fromEntries(Object.entries(dm).filter(([, v]) => v != null)) : null;

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hit = (m: NodeManifest) =>
      !needle || `${m.label} ${m.type} ${m.description ?? ""} ${m.category}`.toLowerCase().includes(needle);
    const out = new Map<string, NodeManifest[]>();
    for (const m of data ?? []) if (hit(m)) out.set(m.category, [...(out.get(m.category) ?? []), m]);
    return [...out.entries()].sort(([a], [b]) => categoryRank(a) - categoryRank(b) || a.localeCompare(b));
  }, [data, q]);

  // workspace tools and scripts are presets of the generic tool / script types (M5-T5)
  const workspaceItems = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const items: WorkspaceItem[] = [
      ...(catalog.data?.tools ?? []).map((t) => ({
        key: `tool:${t.import_path}`, type: "tool.python", label: t.name, hint: "workspace tool",
        preset: { name: t.name, config: { import_path: t.import_path } },
      })),
      ...(catalog.data?.scripts ?? []).map((sc) => ({
        key: `script:${sc.import_path}`, type: "core.script", label: sc.name, hint: "workspace script",
        preset: { name: sc.name, config: { import_path: sc.import_path } },
      })),
    ];
    return items.filter((i) => !needle || `${i.label} ${i.hint}`.toLowerCase().includes(needle));
  }, [catalog.data, q]);

  return (
    <aside className="palette" aria-label="Node palette">
      <input className="search" placeholder="Search blocks…" value={q} onChange={(e) => setQ(e.target.value)}
        aria-label="Search widgets" />
      {!q && (
        <p className="start-tip">
          Drag a block onto the canvas. <b>Steps</b> (solid border) run in order; <b>add-ons</b> (dashed) such as an AI model
          plug into a step. Hover a block to learn what it does.
        </p>
      )}
      <QueryStatus q={nodeTypes} what="widgets" />
      {workspaceItems.length > 0 && (
        <section>
          <h4>Workspace</h4>
          {workspaceItems.map((i) => (
            <div
              key={i.key} className="palette-item kind-workspace" draggable role="button" tabIndex={0}
              title={i.preset.config?.import_path as string} data-testid={`palette-ws-${i.label}`}
              onDragStart={(e) => {
                e.dataTransfer.setData(DND_MIME, `${i.type}\n${JSON.stringify(i.preset)}`);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDoubleClick={() => addElement(i.type, undefined, i.preset)}
              onKeyDown={(e) => { if (e.key === "Enter") addElement(i.type, undefined, i.preset); }}
            >
              <b>{i.label}</b>
              <small>{i.hint}</small>
            </div>
          ))}
        </section>
      )}
      {groups.map(([cat, items]) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {items.map((m) => (
            <div
              key={m.type} className={`palette-item kind-${m.kind}`} draggable role="button" tabIndex={0}
              title={`${m.description ?? ""}\n(${m.type})`} data-testid={`palette-${m.type}`}
              style={{ borderLeftColor: m.color ?? "#64748b" }}
              onDragStart={(e) => { e.dataTransfer.setData(DND_MIME, m.type); e.dataTransfer.effectAllowed = "move"; }}
              onDoubleClick={() => addElement(m.type)}
              onKeyDown={(e) => { if (e.key === "Enter") addElement(m.type); }}
            >
              <b>{m.label}</b>
              <small className="blurb">{firstSentence(m.description) || m.type}{m.provenance === "workspace" ? " · workspace" : ""}</small>
            </div>
          ))}
        </section>
      ))}
    </aside>
  );
}
