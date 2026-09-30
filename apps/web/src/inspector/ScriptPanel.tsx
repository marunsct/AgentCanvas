/** Script nodes (FR-SCR-01): scaffold `scripts/<name>.py` for this node, or open its file in the workspace. */
import { useState } from "react";
import { Link } from "react-router";
import { ApiError, apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { useEditor } from "../editor/store";
import type { IRNode } from "../ir/model";

export function ScriptPanel({ node }: { node: IRNode }) {
  const dispatch = useEditor((s) => s.dispatch);
  const [error, setError] = useState<string | null>(null);
  const cfg = (node.config ?? {}) as { import_path?: string; writes?: string[] };
  const module = cfg.import_path?.split(":")[0];
  const file = module?.startsWith("scripts.") ? `${module.replaceAll(".", "/")}.py` : null;

  const create = async () => {
    setError(null);
    let importPath: string | undefined;
    try {
      const out = await apiFetch<{ import_path: string }>(`/projects/${PROJECT_ID}/scripts`, {
        method: "POST", json: { name: node.name, writes: cfg.writes ?? [] },
      });
      importPath = out.import_path;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) importPath = (e.problem as { import_path?: string }).import_path;
      else setError((e as Error).message);
    }
    if (importPath) dispatch([{ op: "UpdateNode", id: node.id, patch: { config: { import_path: importPath } } }]);
  };

  return (
    <div className="debug-aid" aria-label="script file">
      {file ? (
        <Link to={`/workspace?file=${encodeURIComponent(file)}`} data-testid="open-script">✎ Open {file} in the workspace</Link>
      ) : (
        <button type="button" onClick={() => void create()}>+ Create scripts/{node.name}.py</button>
      )}
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}
