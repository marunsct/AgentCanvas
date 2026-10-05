/** "Browse tools" for an MCP server resource (M3-T5): list the server's tools, pick some, flag destructive ones. */
import { useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { useEditor } from "../editor/store";
import type { IRResource } from "../ir/model";

export interface McpTool {
  name: string;
  description?: string;
  destructive?: boolean;
  read_only?: boolean;
}

export function McpBrowser({ resource }: { resource: IRResource }) {
  const dispatch = useEditor((s) => s.dispatch);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cfg = (resource.config ?? {}) as { discovered?: McpTool[]; tool_filter?: string[] };
  const tools = cfg.discovered ?? [];
  const filter = new Set(cfg.tool_filter ?? []);
  const save = (patch: Record<string, unknown>) =>
    dispatch([{ op: "UpdateResource", id: resource.id, patch: { config: patch } }]);

  const browse = async () => {
    setBusy(true);
    setError(null);
    try {
      const found = await apiFetch<McpTool[]>(`/mcp/inspect?project_id=${encodeURIComponent(PROJECT_ID)}`, {
        method: "POST", json: { name: resource.name, config: { ...resource.config, discovered: undefined } },
      });
      save({ discovered: found });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (name: string) => {
    const next = filter.has(name) ? [...filter].filter((n) => n !== name) : [...filter, name];
    save({ tool_filter: next.length ? next : null });
  };

  return (
    <div className="mcp-browser" aria-label="MCP tools">
      <button type="button" onClick={() => void browse()} disabled={busy}>{busy ? "Connecting…" : "Browse tools"}</button>
      {error && <p className="error-text" role="alert">{error}</p>}
      {tools.length > 0 && (
        <>
          <p className="muted small">{filter.size ? `${filter.size} of ${tools.length} tools offered` : `all ${tools.length} tools offered`}</p>
          <ul>
            {tools.map((t) => (
              <li key={t.name}>
                <label className="row" title={t.description}>
                  <input type="checkbox" checked={filter.size === 0 || filter.has(t.name)}
                    onChange={() => toggle(t.name)} aria-label={`offer ${t.name}`} />
                  <code>{t.name}</code>
                  {t.destructive && <span className="badge err" title="the server marks this tool destructive">destructive</span>}
                  {t.read_only && <span className="badge">read-only</span>}
                </label>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
