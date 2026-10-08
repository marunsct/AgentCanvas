/** Open Agent Spec (S16): download this workflow as an Agent Spec flow, or import one as a new workflow. */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";

interface ExportOut { spec: Record<string, unknown>; report: string[] }
export interface ImportOut { id: string; name: string; report: string[] }

function save(doc: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** A file name from a workflow name: "Ticket triage" -> "ticket-triage.agentspec.json". */
export function specFileName(name: string): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return `${base || "workflow"}.agentspec.json`;
}

export function AgentSpecExport({ wfId, name }: { wfId: string; name: string }) {
  const [report, setReport] = useState<string[] | null>(null);
  const run = useMutation({
    mutationFn: () => apiFetch<ExportOut>(`/workflows/${wfId}/agent-spec`),
    onSuccess: (out) => { save(out.spec, specFileName(name)); setReport(out.report); },
  });
  return (
    <div className="small" data-testid="agentspec-export">
      <button type="button" className="link" onClick={() => run.mutate()} disabled={run.isPending}
        title="Open Agent Spec: a framework-neutral file other agent runtimes can load">
        ⬇ Download as an Agent Spec file…</button>
      {run.error && <p className="error-text" role="alert">{(run.error as Error).message}</p>}
      {report && (
        <div className="notes" data-testid="agentspec-report">
          {report.length === 0
            ? <p className="muted">Everything in this workflow is in the file.</p>
            : <><p className="muted">Downloaded. Agent Spec cannot say everything; AgentCanvas reads the file back exactly, other tools will see:</p>
              <ul>{report.map((r) => <li key={r}>{r}</li>)}</ul></>}
        </div>
      )}
    </div>
  );
}

/** Reads a chosen file and imports it; `onDone` gets the new workflow and the import notes. */
export function AgentSpecImport({ projectId, onDone }: { projectId: string; onDone: (out: ImportOut) => void }) {
  const run = useMutation({
    mutationFn: async (file: File) => {
      let spec: unknown;
      try {
        spec = JSON.parse(await file.text());
      } catch {
        throw new Error(`${file.name} is not a JSON file. Agent Spec files are JSON (YAML is not supported yet).`);
      }
      return apiFetch<ImportOut>(`/projects/${projectId}/workflows/agent-spec`, { method: "POST", json: { spec } });
    },
    onSuccess: onDone,
  });
  return (
    <div className="row gap small" data-testid="agentspec-import">
      <label className="button" htmlFor="agentspec-file">Import an Agent Spec file…</label>
      <input id="agentspec-file" type="file" accept=".json,application/json" className="visually-hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) run.mutate(f); e.target.value = ""; }} />
      <span className="muted">Flows and agents in Open Agent Spec, e.g. from WayFlow or pyagentspec's LangGraph and AutoGen adapters.</span>
      {run.isPending && <span className="muted">Importing…</span>}
      {run.error && <p className="error-text" role="alert">{(run.error as Error).message}</p>}
    </div>
  );
}
