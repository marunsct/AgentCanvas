/** The workflow editor: palette | canvas | inspector/run/code, with the problems dock below. */
import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { useNodeTypes } from "../api/queries";
import { Canvas } from "../canvas/Canvas";
import { Palette } from "../canvas/Palette";
import { CodePanel } from "../code/CodePanel";
import { Inspector } from "../inspector/Inspector";
import { ProblemsPanel } from "../problems/ProblemsPanel";
import { RunPanel } from "../run/RunPanel";
import { useRun } from "../run/runStore";
import { useEditor } from "./store";

type Tab = "inspect" | "run" | "code";

export function EditorPage() {
  const { id } = useParams();
  const wf = useEditor((s) => s.wf);
  const notice = useEditor((s) => s.notice);
  const saving = useEditor((s) => s.pending.length > 0 || s.inflight);
  const [tab, setTab] = useState<Tab>("inspect");
  const [error, setError] = useState<string | null>(null);
  const nodeTypes = useNodeTypes();

  useEffect(() => {
    if (!id) return;
    useEditor.getState().reset();
    useRun.getState().reset();
    useEditor.getState().load(id).catch((e: Error) => setError(e.message));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, .code-view")) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) useEditor.getState().redo();
        else useEditor.getState().undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) return <div className="page"><p className="error-text">Could not load workflow: {error}</p><Link to="/">Back</Link></div>;
  if (!wf || !nodeTypes.data) return <div className="page muted">Loading workflow…</div>;

  return (
    <div className="editor">
      <header className="topbar">
        <Link to="/" className="brand">AgentCanvas</Link>
        <span className="crumb">/ {wf.name}</span>
        <span className={`save-state${saving ? " saving" : ""}`}>{saving ? "Saving…" : "Saved"}</span>
        <span className="spacer" />
        <button type="button" onClick={() => useEditor.getState().undo()} title="Undo (Ctrl/⌘+Z)">↶</button>
        <button type="button" onClick={() => useEditor.getState().redo()} title="Redo (Ctrl/⌘+Shift+Z)">↷</button>
        <button type="button" className="primary" onClick={() => setTab("run")}>▶ Test</button>
      </header>
      {notice && (
        <div className={`toast ${notice.kind}`} role="status">
          {notice.text} <button type="button" className="link" onClick={() => useEditor.getState().setNotice(null)}>dismiss</button>
        </div>
      )}
      <Palette />
      <main className="stage">
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
      </main>
      <aside className="side">
        <nav className="tabs" role="tablist">
          {(["inspect", "run", "code"] as Tab[]).map((t) => (
            <button type="button" role="tab" key={t} aria-selected={tab === t} className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}>{{ inspect: "Inspector", run: "Run", code: "Code" }[t]}</button>
          ))}
        </nav>
        {tab === "inspect" && <Inspector />}
        {tab === "run" && <RunPanel />}
        {tab === "code" && <CodePanel />}
      </aside>
      <footer className="dock"><ProblemsPanel /></footer>
    </div>
  );
}
