/** Workspace (M5-T1/T2): the project's code — scripts, tools, custom nodes, events — with ruff diagnostics. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { CodeEditor, type LintItem } from "./CodeEditor";
import { SecretsPanel } from "./SecretsPanel";
import { QueryStatus } from "../ui/QueryStatus";

interface FileOut {
  path: string;
  size: number;
  modified: number;
}

interface FileContent {
  path: string;
  content: string;
  diagnostics: LintItem[];
}

const base = `/projects/${PROJECT_ID}`;
const POLICY = "sandbox.json";
// FR-X-08: egress allow-list, header injection by the LangSmith auth proxy ({{ secret.NAME }}), limits
const POLICY_TEMPLATE = `${JSON.stringify({ egress: [], inject: [], timeout_s: 30, memory_mb: 512 }, null, 2)}\n`;
const enc = (path: string) => path.split("/").map(encodeURIComponent).join("/");

export function WorkspacePage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("file");
  const files = useQuery({ queryKey: ["files"], queryFn: () => apiFetch<FileOut[]>(`${base}/files`) });
  const file = useQuery({
    queryKey: ["file", selected], enabled: Boolean(selected),
    queryFn: () => apiFetch<FileContent>(`${base}/files/${enc(selected!)}`),
  });
  const [draft, setDraft] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<LintItem[]>([]);
  const [newPath, setNewPath] = useState("");
  useEffect(() => { setDraft(null); setDiagnostics(file.data?.diagnostics ?? []); }, [file.data]);

  const save = useMutation({
    mutationFn: (content: string) =>
      apiFetch<{ diagnostics: LintItem[] }>(`${base}/files/${enc(selected!)}`, { method: "PUT", json: { content } }),
    onSuccess: (out) => {
      setDiagnostics(out.diagnostics);
      setDraft(null);
      void qc.invalidateQueries({ queryKey: ["files"] });
      void qc.invalidateQueries({ queryKey: ["workspace-catalog"] }); // new tools / custom nodes
      void qc.invalidateQueries({ queryKey: ["node-types"] });
    },
  });
  const create = useMutation({
    mutationFn: ({ path, content = "" }: { path: string; content?: string }) =>
      apiFetch(`${base}/files/${enc(path)}`, { method: "PUT", json: { content } }),
    onSuccess: (_, { path }) => { setNewPath(""); void qc.invalidateQueries({ queryKey: ["files"] }); setParams({ file: path }); },
  });
  const openPolicy = () => {
    if (files.data?.some((f) => f.path === POLICY)) setParams({ file: POLICY });
    else create.mutate({ path: POLICY, content: POLICY_TEMPLATE });
  };
  const remove = useMutation({
    mutationFn: (path: string) => apiFetch(`${base}/files/${enc(path)}`, { method: "DELETE" }),
    onSuccess: () => { setParams({}); void qc.invalidateQueries({ queryKey: ["files"] }); },
  });
  const dirty = draft !== null && draft !== file.data?.content;
  const doSave = () => { if (selected && draft !== null) save.mutate(draft); };

  return (
    <div className="page workspace-page">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Workspace</span></header>
      <div className="workspace-layout">
        <aside aria-label="workspace files">
          <form className="row gap" onSubmit={(e) => { e.preventDefault(); if (newPath.trim()) create.mutate({ path: newPath.trim() }); }}>
            <input placeholder="scripts/new_step.py" value={newPath} onChange={(e) => setNewPath(e.target.value)} aria-label="new file path" />
            <button type="submit">New</button>
          </form>
          {create.error && <p className="error-text small">{(create.error as Error).message}</p>}
          <button type="button" className="link" onClick={openPolicy}>Sandbox policy</button>
          <QueryStatus q={files} what="workspace files" />
          <ul className="file-tree">
            {files.data?.map((f) => (
              <li key={f.path}>
                <button type="button" className={f.path === selected ? "active" : ""} onClick={() => setParams({ file: f.path })}>
                  {f.path}
                </button>
              </li>
            ))}
          </ul>
          <SecretsPanel />
        </aside>
        <section aria-label="editor">
          {selected && !file.data && <QueryStatus q={file} what={selected} />}
          {selected && file.data ? (
            <>
              <div className="row gap">
                <code>{selected}</code>{dirty && <span className="badge warn">unsaved</span>}
                <span className="spacer" />
                <button type="button" className="primary" onClick={doSave} disabled={!dirty || save.isPending}>Save</button>
                <button type="button" className="danger" onClick={() => { if (window.confirm(`Delete ${selected}?`)) remove.mutate(selected); }}>
                  Delete
                </button>
              </div>
              <CodeEditor path={selected} value={file.data.content} diagnostics={diagnostics} onChange={setDraft} onSave={doSave} />
              <ul className="lint-list" data-testid="lint">
                {diagnostics.map((d, i) => <li key={i}><b>{d.code}</b> line {d.line}: {d.message}</li>)}
                {diagnostics.length === 0 && <li className="muted">{selected.endsWith(".py") ? "ruff: no problems" : "no problems"}</li>}
              </ul>
              {save.error && <p className="error-text">{(save.error as Error).message}</p>}
            </>
          ) : (
            <p className="muted">Pick a file. Scripts go in <code>scripts/</code>, tools in <code>tools/</code>, custom widgets in{" "}
              <code>nodes/</code> and event handlers in <code>events/</code>.</p>
          )}
        </section>
      </div>
    </div>
  );
}
