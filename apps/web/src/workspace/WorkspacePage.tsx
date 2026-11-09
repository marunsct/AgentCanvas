/** Workspace (M5-T1/T2): the project's code — scripts, tools, custom nodes, events — with ruff diagnostics.
 *
 * S17: opened from a workflow (`/workflows/:id/workspace`) it shows that workflow's own code folder and the shared
 * code, offers a way back to the workflow, starter code for new files and the Copilot's help with code. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type BlockerFunction, Link, useBlocker, useParams, useSearchParams } from "react-router";
import { ApiError, apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { CodeEditor, type EditorApi, type LintItem } from "./CodeEditor";
import { CodeHelp } from "./CodeHelp";
import { FileTree } from "./FileTree";
import { KIND_LABEL, starter } from "./starters";
import { QueryStatus } from "../ui/QueryStatus";
import type { FileOut, FileContent, FilesOut } from "../api/generated";
import { confirmDialog, promptDialog, toast } from "../ui/dialog";
import { PageHeader } from "../ui/PageHeader";



// read per request: the project is chosen after sign-in, and may change
const base = () => `/projects/${PROJECT_ID}`;
const POLICY = "sandbox.json";
// FR-X-08: egress allow-list, header injection by the LangSmith auth proxy ({{ secret.NAME }}), limits
const POLICY_TEMPLATE = `${JSON.stringify({ egress: [], inject: [], timeout_s: 30, memory_mb: 512 }, null, 2)}\n`;
const enc = (path: string) => path.split("/").map(encodeURIComponent).join("/");
/** A file as read; `version` (a hash of its text) goes back as If-Match on save so a newer copy is not overwritten. */
type Opened = FileContent & { version?: string };
/** The server copy the editor's text started from. */
interface Base { path: string; version?: string; content: string }


export function WorkspacePage() {
  const qc = useQueryClient();
  const { id: workflowId = null } = useParams();
  const [params, setParams] = useSearchParams();
  const selected = params.get("file");
  const workflow = useQuery({
    queryKey: ["workflow-name", workflowId], enabled: Boolean(workflowId),
    queryFn: () => apiFetch<{ name: string }>(`/workflows/${workflowId}`),
  });
  const files = useQuery({
    queryKey: ["files", PROJECT_ID, workflowId],
    queryFn: () => workflowId
      ? apiFetch<FilesOut>(`${base()}/workflow-files/${encodeURIComponent(workflowId)}`)
      : apiFetch<FileOut[]>(`${base()}/files`).then((list) => ({ files: list, folder: null })),
  });
  const folder = files.data?.folder ?? null;
  const [own, shared] = useMemo(() => {
    const all = files.data?.files ?? [];
    return folder ? [all.filter((f) => f.path.startsWith(`${folder}/`)), all.filter((f) => !f.path.startsWith(`${folder}/`))] : [[], all];
  }, [files.data, folder]);
  const editorApi = useRef<EditorApi | null>(null);
  const file = useQuery({
    queryKey: ["file", PROJECT_ID, selected], enabled: Boolean(selected),
    queryFn: () => apiFetch<Opened>(`${base()}/files/${enc(selected!)}`),
  });
  // the editor's text once it was typed in, for the file it was typed in; a save does not clear it: text typed while
  // the save was on its way would then count as saved (dirty compares it with what the server has)
  const [edit, setEdit] = useState<{ path: string; text: string } | null>(null);
  const draft = edit && edit.path === selected ? edit.text : null;
  const [origin, setOrigin] = useState<Base | null>(null);
  // a file opened again starts from what the server has
  useEffect(() => { setEdit(null); setOrigin(null); }, [selected]);
  const [diagnostics, setDiagnostics] = useState<LintItem[]>([]);
  const [newPath, setNewPath] = useState("");
  useEffect(() => {
    const d = file.data;
    setDiagnostics(d?.diagnostics ?? []);
    if (!d) return;
    // a newer copy (reopened from the cache, then refetched; or changed by the Copilot, a teammate, a Git pull) is
    // shown when nothing was typed; otherwise the typed text stays and the page says the file changed
    setOrigin((o) => {
      const next = { path: d.path, version: d.version, content: d.content };
      if (!o || o.path !== d.path) return next;
      if (o.version === d.version && o.content === d.content) return o;
      return (draft ?? o.content) === o.content || draft === d.content ? next : o;
    });
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- only a new copy of the file decides; draft is read then
  }, [file.data]);
  const stale = Boolean(file.data && origin && origin.path === selected && origin.version !== file.data.version);

  const save = useMutation({
    mutationFn: ({ path, content, version }: { path: string; content: string; version?: string }) =>
      apiFetch<{ diagnostics: LintItem[]; version?: string }>(`${base()}/files/${enc(path)}`, {
        method: "PUT", json: { content }, headers: version ? { "If-Match": `"${version}"` } : undefined,
      }),
    onSuccess: (out, { path, content }) => {
      if (path === selected) { setOrigin({ path, version: out.version, content }); setDiagnostics(out.diagnostics); }
      // the cached copy is what reopening the file shows: keep it in step with what was saved
      qc.setQueryData<Opened>(["file", PROJECT_ID, path], (old) => ({ ...old, path, content, diagnostics: out.diagnostics, version: out.version }));
      void qc.invalidateQueries({ queryKey: ["files", PROJECT_ID] });
      void qc.invalidateQueries({ queryKey: ["workspace-catalog"] }); // new tools / custom nodes
      void qc.invalidateQueries({ queryKey: ["node-types"] });
    },
    // changed elsewhere since it was opened: fetch the newer copy, which brings up the choice below
    onError: (e, { path }) => { if (e instanceof ApiError && e.status === 409) void qc.invalidateQueries({ queryKey: ["file", PROJECT_ID, path] }); },
  });
  /** Show the server's newer copy in place of the typed text (an undoable edit). */
  const takeTheirs = () => {
    if (!file.data) return;
    editorApi.current?.setText(file.data.content);
    setOrigin({ path: file.data.path, version: file.data.version, content: file.data.content });
    save.reset();
  };
  /** Keep the typed text: the next Save replaces the newer copy. */
  const keepMine = () => {
    if (!file.data) return;
    setOrigin({ path: file.data.path, version: file.data.version, content: file.data.content });
    save.reset();
  };
  const create = useMutation({
    mutationFn: ({ path, content = "" }: { path: string; content?: string }) =>
      apiFetch(`${base()}/files/${enc(path)}`, { method: "PUT", json: { content } }),
    onSuccess: (_, { path }) => {
      setNewPath("");
      void qc.invalidateQueries({ queryKey: ["files", PROJECT_ID] });
      void qc.invalidateQueries({ queryKey: ["workspace-catalog"] });
      setParams({ file: path });
    },
  });
  const openPolicy = async () => {
    if (!(await leave())) return;
    if (files.data?.files.some((f) => f.path === POLICY)) setParams({ file: POLICY });
    else create.mutate({ path: POLICY, content: POLICY_TEMPLATE });
  };
  const move = useMutation({
    mutationFn: ({ path, to }: { path: string; to: string }) =>
      apiFetch<FileOut>(`${base()}/files-move`, { method: "POST", json: { path, to } }),
    onSuccess: (out) => {
      toast(`Moved to ${out.path}`);
      setParams({ file: out.path });
      void qc.invalidateQueries({ queryKey: ["files", PROJECT_ID] });
      void qc.invalidateQueries({ queryKey: ["workspace-catalog"] });
    },
  });
  const askMove = async () => {
    if (!selected || !(await leave())) return;
    const to = (await promptDialog({
      title: "Rename or move the file", confirm: "Move",
      body: "Steps and tools that use it by its old path stop finding it until you update them.",
      input: { label: "New path", value: selected, required: true },
    }))?.trim();
    if (to && to !== selected) move.mutate({ path: selected, to });
  };
  const remove = useMutation({
    mutationFn: (path: string) => apiFetch(`${base()}/files/${enc(path)}`, { method: "DELETE" }),
    onSuccess: () => { setParams({}); void qc.invalidateQueries({ queryKey: ["files", PROJECT_ID] }); },
  });
  const dirty = draft !== null && draft !== file.data?.content;
  // leaving a file with unsaved changes (another file, a new one, another page, Back, or closing the tab) asks first
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const leave = async () => !dirty || await confirmDialog({
    title: `Discard your changes to ${selected}?`, body: "They are not saved yet.", confirm: "Discard changes",
    cancel: "Keep editing", danger: true,
  });
  // another page (a link, the crumb, Cmd+K) or Back/Forward; this page's own file switches asked already (`leave`)
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation, historyAction }) =>
      dirty && (currentLocation.pathname !== nextLocation.pathname || historyAction === "POP"),
    [dirty],
  );
  const blocker = useBlocker(shouldBlock);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    void leave().then((ok) => (ok ? blocker.proceed() : blocker.reset()));
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- once per blocked navigation
  }, [blocker.state]);
  const open = async (path: string) => { if (path !== selected && await leave()) setParams({ file: path }); };
  const doSave = () => {
    if (selected && draft !== null && dirty && !save.isPending && origin?.path === selected) {
      save.mutate({ path: selected, content: draft, version: origin.version });
    }
  };
  const newFile = async (path: string) => { if (await leave()) create.mutate({ path, content: starter(path) }); };
  const prefix = folder ? `${folder}/` : "";

  return (
    <div className="page workspace-page">
      <PageHeader title="Workspace"
        crumbs={workflowId ? [{ to: "/", label: "Workflows" }, { to: `/workflows/${workflowId}`, label: workflow.data?.name ?? "Workflow", testId: "back-to-workflow" }] : undefined}
        description={workflowId ? "This workflow's own code (scripts, tools, custom blocks), next to the project's shared code." : "The project's code: scripts, tools, custom blocks and event handlers that workflows use."} />
      <div className="workspace-layout">
        <aside aria-label="workspace files">
          <form className="row gap new-file" onSubmit={(e) => { e.preventDefault(); if (newPath.trim()) void newFile(newPath.trim()); }}>
            <input placeholder={`${prefix}scripts/new_step.py`} value={newPath} onChange={(e) => setNewPath(e.target.value)} aria-label="new file path" />
            <button type="submit">New file</button>
          </form>
          <div className="row gap wrap" role="group" aria-label="start a new file">
            {(["scripts", "tools", "nodes"] as const).map((k) => (
              <button type="button" key={k} className="chip" onClick={() => setNewPath(`${prefix}${k}/`)}
                title={`Start a ${KIND_LABEL[k].toLowerCase()} in ${prefix || "the shared "}${k}/`}>+ {KIND_LABEL[k]}</button>
            ))}
          </div>
          {create.error && <p className="error-text small">{(create.error as Error).message}</p>}
          <QueryStatus q={files} what="workspace files" />
          {folder && (
            <>
              <h4 className="tree-head">This workflow's code <span className="muted small">{folder}/</span></h4>
              <FileTree label="this workflow's files" files={own} strip={prefix} selected={selected} onOpen={(path) => void open(path)} />
              <h4 className="tree-head">Shared code <span className="muted small">every workflow can use it</span></h4>
            </>
          )}
          <FileTree label={folder ? "shared files" : "workspace files"} files={shared} selected={selected} onOpen={(path) => void open(path)} />
          <p className="muted small secrets-moved">API keys and passwords for this code are under <Link to="/settings/secrets" data-testid="secrets-link">Settings › Secrets</Link>.
            What it may reach is the sandbox policy: <Link to="/settings/sandbox">Settings › Sandbox</Link>, or
            {" "}<button type="button" className="link" onClick={() => void openPolicy()} aria-label="sandbox.json: open the sandbox policy as a file">sandbox.json</button> as a file.</p>
        </aside>
        <section aria-label="editor">
          {selected && !file.data && <QueryStatus q={file} what={selected} />}
          {selected && file.data && origin?.path === selected ? (
            <>
              <div className="row gap">
                <code>{selected}</code>{dirty && <span className="badge warn">unsaved</span>}
                <span className="spacer" />
                <button type="button" className="primary" onClick={doSave} disabled={!dirty || save.isPending}>Save</button>
                <button type="button" onClick={() => void askMove()} disabled={move.isPending}>Rename / move…</button>
                <button type="button" className="danger" onClick={async () => { if (await confirmDialog({ title: `Delete ${selected}?`, confirm: "Delete", danger: true })) remove.mutate(selected); }}>
                  Delete
                </button>
              </div>
              {stale && (
                <div className="callout warn row gap wrap" role="alert" data-testid="file-changed">
                  <span>{selected} was changed elsewhere since you opened it.</span>
                  <button type="button" onClick={takeTheirs} title="Your unsaved changes are replaced (Undo brings them back)">Load the new version</button>
                  <button type="button" onClick={keepMine} title="Save then replaces the new version with yours">Keep mine</button>
                </div>
              )}
              <CodeEditor path={selected} value={origin.content} diagnostics={diagnostics} onChange={(text) => setEdit({ path: selected, text })} onSave={doSave} api={editorApi} />
              <ul className="lint-list" data-testid="lint">
                {diagnostics.map((d, i) => <li key={i}><b>{d.code}</b> line {d.line}: {d.message}</li>)}
                {diagnostics.length === 0 && <li className="muted">{selected.endsWith(".py") ? "ruff: no problems" : "no problems"}</li>}
              </ul>
              {save.error && <p className="error-text">{(save.error as Error).message}</p>}
              {remove.error && <p className="error-text" role="alert">Could not delete {selected}: {(remove.error as Error).message}</p>}
              {move.error && <p className="error-text" role="alert">Could not move {selected}: {(move.error as Error).message}</p>}
              {selected.endsWith(".py") && (
                <CodeHelp workflowId={workflowId} path={selected} content={draft ?? origin.content} diagnostics={diagnostics}
                  selection={() => editorApi.current?.selection() ?? ""} onAccept={(code) => editorApi.current?.setText(code)} />
              )}
            </>
          ) : (
            <p className="muted">Pick a file, or start one with the buttons on the left.{" "}
              {folder ? <>This workflow's own code goes in <code>{folder}/</code>; code in the shared folders is used by every workflow.{" "}</> : null}
              Scripts go in <code>scripts/</code>, tools in <code>tools/</code>, custom blocks in{" "}
              <code>nodes/</code> and event handlers in <code>events/</code>.</p>
          )}
        </section>
      </div>
    </div>
  );
}
