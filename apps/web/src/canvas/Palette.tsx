/** Node palette (M2-T4): categories from the registry, search, drag onto the canvas (or click to add). */
import { useMemo, useState } from "react";
import { useEditor } from "../editor/store";
import { useMcpConnectors, useNodeTypes, useSetup, useWorkspaceCatalog } from "../api/queries";
import type { McpConnector } from "../api/generated";
import type { NodeManifest } from "../api/types";
import { addElement, DND_MIME, manifestCache, type Preset, projectModel } from "./Canvas";
import { QueryStatus } from "../ui/QueryStatus";
import { categoryRank, firstSentence } from "../help/glossary";
import { paletteTypes } from "../editor/simple";
import { useUi } from "../editor/ui";
import { onActivate } from "./keys";

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
  const catalog = useWorkspaceCatalog(useEditor((s) => s.workflowId));
  const connectors = useMcpConnectors();
  const [q, setQ] = useState("");
  const simple = useUi((st) => st.simple);
  for (const m of data ?? []) manifestCache.set(m.type, m);
  const dm = useSetup().data?.default_model;
  projectModel.current = dm ? Object.fromEntries(Object.entries(dm).filter(([, v]) => v != null)) : null;

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const hit = (m: NodeManifest) =>
      !needle || `${m.label} ${m.type} ${m.description ?? ""} ${m.category}`.toLowerCase().includes(needle);
    const out = new Map<string, NodeManifest[]>();
    for (const m of paletteTypes(data ?? [], simple)) if (hit(m)) out.set(m.category, [...(out.get(m.category) ?? []), m]);
    return [...out.entries()].sort(([a], [b]) => categoryRank(a) - categoryRank(b) || a.localeCompare(b));
  }, [data, q, simple]);

  // workspace tools and scripts are presets of the generic tool / script types (M5-T5)
  const workspaceItems = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const items: WorkspaceItem[] = [
      ...(catalog.data?.tools ?? []).map((t) => ({
        key: `tool:${t.import_path}`, type: "tool.python", label: t.name, hint: t.workflow ? "this workflow's tool" : "shared workspace tool",
        preset: { name: t.name, config: { import_path: t.import_path } },
      })),
      ...(catalog.data?.scripts ?? []).map((sc) => ({
        key: `script:${sc.import_path}`, type: "core.script", label: sc.name, hint: sc.workflow ? "this workflow's script" : "shared workspace script",
        preset: { name: sc.name, config: { import_path: sc.import_path } },
      })),
    ];
    return items.filter((i) => !needle || `${i.label} ${i.hint}`.toLowerCase().includes(needle));
  }, [catalog.data, q]);

  // the connector gallery: each one is an App connector (MCP) block, filled in (S15)
  const connectorItems = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (connectors.data ?? []).filter(
      (c) => !needle || `${c.name} ${c.category} ${c.description} mcp connector`.toLowerCase().includes(needle),
    );
  }, [connectors.data, q]);
  const connectorPreset = (c: McpConnector): Preset => ({ name: c.id.replace(/[^a-z0-9_]/gi, "_"), config: c.config });

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
      {simple && !q && <p className="muted small" data-testid="simple-note">Simple mode shows the blocks most workflows need. Switch to <b>Full</b> at the top for every block.</p>}
      {!simple && workspaceItems.length > 0 && (
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
              onKeyDown={onActivate(() => addElement(i.type, undefined, i.preset))}
            >
              <b>{i.label}</b>
              <small>{i.hint}</small>
            </div>
          ))}
        </section>
      )}
      {!simple && connectorItems.length > 0 && (
        <details className="palette-connectors" open={!!q.trim()} data-testid="palette-connectors">
          <summary><h4>App connectors ({connectorItems.length})</h4></summary>
          {connectorItems.map((c) => (
            <div
              key={c.id} className="palette-item kind-resource" draggable role="button" tabIndex={0}
              title={`${c.description}${c.setup ? `\n${c.setup}` : ""}`} data-testid={`palette-connector-${c.id}`}
              aria-label={`${c.name} connector`}
              onDragStart={(e) => {
                e.dataTransfer.setData(DND_MIME, `mcp.server\n${JSON.stringify(connectorPreset(c))}`);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDoubleClick={() => addElement("mcp.server", undefined, connectorPreset(c))}
              onKeyDown={onActivate(() => addElement("mcp.server", undefined, connectorPreset(c)))}
            >
              <b>{c.name}</b>
              <small className="blurb">
                {c.description}
                {(c.secrets ?? []).length > 0 && ` · needs ${(c.secrets ?? []).map((x) => x.name).join(", ")}`}
                {c.local && " · local edition"}
                {c.auth === "oauth" && " · sign-in not supported yet"}
              </small>
            </div>
          ))}
        </details>
      )}
      {groups.map(([cat, items]) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {items.map((m) => (
            <div
              key={m.type} className={`palette-item kind-${m.kind}`} draggable role="button" tabIndex={0}
              title={`${m.description ?? ""}\n(${m.type})`} data-testid={`palette-${m.type}`} aria-label={m.label}
              style={{ borderLeftColor: m.color ?? "#64748b" }}
              onDragStart={(e) => { e.dataTransfer.setData(DND_MIME, m.type); e.dataTransfer.effectAllowed = "move"; }}
              onDoubleClick={() => addElement(m.type)}
              onKeyDown={onActivate(() => addElement(m.type))}
            >
              <b>{m.label}{m.stability && m.stability !== "stable" && (
                <span className="badge beta" title="This block may still change; its settings or behaviour can differ in a later version">
                  {m.stability}</span>)}</b>
              <small className="blurb">{firstSentence(m.description) || m.type}{m.provenance === "workspace" ? " · workspace" : ""}</small>
            </div>
          ))}
        </section>
      ))}
    </aside>
  );
}
