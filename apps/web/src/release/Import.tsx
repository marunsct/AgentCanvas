/** Import (S31): a Langflow, Flowise, n8n or Open Agent Spec file, or OpenAI Agents SDK code (what OpenAI's Agent
 * Builder exports), becomes a new workflow. */
import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { Imported as ImportOut } from "../api/generated";


export const FORMAT_LABELS: Record<string, string> = {
  langflow: "Langflow", flowise: "Flowise", n8n: "n8n", agentspec: "Open Agent Spec", openai_agents: "OpenAI Agents SDK",
};

/** A workflow name from a file name: "customer-support.flow.json" -> "Customer support". */
export function nameFromFile(file: string): string {
  const base = file.replace(/\.(json|txt|py)$/i, "").replace(/\.(flow|agentspec|workflow)$/i, "").replace(/[-_.]+/g, " ").trim();
  return base ? base[0].toUpperCase() + base.slice(1) : "";
}

/** Reads a chosen file and imports it; `onDone` gets the new workflow and the import notes. */
export function FileImport({ projectId, onDone }: { projectId: string; onDone: (out: ImportOut) => void }) {
  const run = useMutation({
    mutationFn: async (file: File) => {
      if (/\.py$/i.test(file.name)) {  // Python: OpenAI Agents SDK code, read by the server, never run
        const name = nameFromFile(file.name) || undefined;
        return apiFetch<ImportOut>(`/projects/${projectId}/workflows/import`, { method: "POST", json: { code: await file.text(), name } });
      }
      let doc: unknown;
      try {
        doc = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} is not a JSON file. Export your flow from Langflow, Flowise or n8n as JSON.`);
      }
      const named = typeof doc === "object" && doc !== null && "name" in doc && typeof doc.name === "string" && doc.name.trim();
      const name = named ? undefined : nameFromFile(file.name) || undefined;  // a file without a name: its file name
      return apiFetch<ImportOut>(`/projects/${projectId}/workflows/import`, { method: "POST", json: { file: doc, name } });
    },
    onSuccess: onDone,
  });
  return (
    <div className="row gap small" data-testid="file-import">
      <label className="button" htmlFor="import-file">Import a file…</label>
      <input id="import-file" type="file" accept=".json,application/json,.py,text/x-python" className="visually-hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) run.mutate(f); e.target.value = ""; }} />
      <span className="muted">A flow exported from Langflow, Flowise or n8n, an Open Agent Spec file (e.g. from WayFlow), or
        OpenAI Agents SDK code (a <code>.py</code> file, as OpenAI&apos;s Agent Builder exports it).</span>
      {run.isPending && <span className="muted">Importing…</span>}
      {run.error && <p className="error-text" role="alert">{(run.error as Error).message}</p>}
    </div>
  );
}

export type { ImportOut };
