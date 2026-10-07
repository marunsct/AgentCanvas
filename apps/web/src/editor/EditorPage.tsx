/** The workflow editor: palette | canvas | inspector/run/code, with the problems dock below. */
import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useNodeTypes } from "../api/queries";
import { Canvas } from "../canvas/Canvas";
import { Palette } from "../canvas/Palette";
import { CodePanel } from "../code/CodePanel";
import { CopilotPanel } from "../copilot/CopilotPanel";
import { useCopilot } from "../copilot/copilot";
import { Inspector } from "../inspector/Inspector";
import { useInbox } from "../inbox/InboxPage";
import { ProblemsPanel } from "../problems/ProblemsPanel";
import { TestsPanel } from "../release/TestsPanel";
import { VersionsPanel } from "../release/VersionsPanel";
import { ExplainView } from "../run/ExplainView";
import { RunPanel } from "../run/RunPanel";
import { useRun } from "../run/runStore";
import { Timeline } from "../run/Timeline";
import { ErrorBoundary, QueryStatus } from "../ui/QueryStatus";
import { Tutor } from "../tutor/Tutor";
import { CommandPalette } from "./CommandPalette";
import { runErrorHelp } from "../help/diagnostics";
import { HelpText } from "../help/HelpText";
import { HelpDrawer, useFirstVisitHelp } from "../help/HelpDrawer";
import { useEditor } from "./store";
import { can, ME } from "../api/session";
import { type DockTab, useUi } from "./ui";

type Tab = "inspect" | "copilot" | "run" | "tests" | "versions" | "code";

export function EditorPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const wf = useEditor((s) => s.wf);
  const notice = useEditor((s) => s.notice);
  const saving = useEditor((s) => s.pending.length > 0 || s.inflight);
  const [tab, setTab] = useState<Tab>("inspect");
  const [error, setError] = useState<string | null>(null);
  const nodeTypes = useNodeTypes();
  const dock = useUi((s) => s.dock);
  const setDock = useUi((s) => s.setDock);
  const blast = useUi((s) => s.blast);
  const inbox = useInbox();
  const pendingDecisions = inbox.data?.length ?? 0;
  useFirstVisitHelp();

  useEffect(() => {
    if (!id) return;
    useEditor.getState().reset();
    useRun.getState().reset();
    useCopilot.setState({ workflowId: null, conversation: null, ghost: null, error: null });
    useEditor.getState().load(id).catch((e: Error) => setError(e.message));
    const thread = params.get("thread");  // "open this run's timeline", e.g. from an experiment's results
    if (thread) {
      useRun.getState().setThread(thread);
      useUi.getState().setDock("timeline");
    }
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select, .code-view")) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        useUi.getState().openPalette();
        return;
      }
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) useEditor.getState().redo();
        else useEditor.getState().undo();
      }
      if (mod && e.key.toLowerCase() === "g") {
        e.preventDefault();
        void useEditor.getState().group?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (error) return <div className="page"><p className="error-text">Could not load workflow: {error}</p><Link to="/">Back</Link></div>;
  if (nodeTypes.isError) return <div className="page"><QueryStatus q={nodeTypes} what="the widget catalogue" /><Link to="/">Back</Link></div>;
  if (!wf || !nodeTypes.data) return <div className="page muted" role="status">Loading workflow…</div>;

  return (
    <div className="editor">
      <header className="topbar">
        <Link to="/" className="brand">AgentCanvas</Link>
        <span className="crumb">/ {wf.name}</span>
        {can("builder")
          ? <span className={`save-state${saving ? " saving" : ""}`}>{saving ? "Saving…" : "Saved"}</span>
          : <span className="save-state" data-testid="view-only" title={`your role is ${ME.role}`}>View only</span>}
        <span className="spacer" />
        <Link to={`/workflows/${wf.id}/evaluation`} className="inbox-link" data-testid="evaluation-link">Evaluate</Link>
        <Link to="/workspace" className="inbox-link" data-testid="workspace-link">Workspace</Link>
        <Link to="/memory" className="inbox-link" data-testid="memory-link">Memory</Link>
        <Link to="/inbox" className="inbox-link" data-testid="inbox-link">
          Inbox{pendingDecisions > 0 && <span className="badge warn">{pendingDecisions}</span>}
        </Link>
        <button type="button" onClick={() => useUi.getState().openPalette()} title="Command palette (Ctrl/⌘+K)">⌘K</button>
        <button type="button" onClick={() => void useEditor.getState().tidy?.()} title="Auto-layout (ELK)">Tidy</button>
        <button type="button" className={blast ? "active" : ""} aria-pressed={blast} onClick={() => useUi.getState().toggleBlast()}
          title="Show what each step can do to the outside world, and whether a human approves it first">Blast radius</button>
        <button type="button" onClick={() => void useEditor.getState().group?.()}
          title="Turn the selected steps into one subgraph step (Ctrl/⌘+G)">⧉ Subgraph</button>
        <button type="button" onClick={() => useEditor.getState().frame?.()}
          title="Draw a labelled frame around the selected steps (layout only — nothing changes at run time)">▭ Frame</button>
        <button type="button" onClick={() => useEditor.getState().undo()} title="Undo (Ctrl/⌘+Z)">↶</button>
        <button type="button" onClick={() => useEditor.getState().redo()} title="Redo (Ctrl/⌘+Shift+Z)">↷</button>
        <button type="button" onClick={() => useUi.getState().setHelp(!useUi.getState().help)} data-testid="help-button"
          title="A short guide for beginners: what the blocks, dots and arrows mean">❓ Help</button>
        <button type="button" onClick={() => setTab("copilot")} data-testid="copilot-button"
          title="Chat with the AI Copilot: describe a workflow, ask questions, get changes to review">✦ Copilot</button>
        <button type="button" className="primary" onClick={() => setTab("run")}>▶ Test</button>
      </header>
      {notice && (
        <div className={`toast ${notice.kind}`} role="status">
          {notice.text}{notice.kind === "error" && <HelpText help={runErrorHelp(notice.text)} className="run-hint" />}
          {" "}<button type="button" className="link" onClick={() => useEditor.getState().setNotice(null)}>dismiss</button>
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
          {(["inspect", "copilot", "run", "tests", "versions", "code"] as Tab[]).map((t) => (
            <button type="button" role="tab" key={t} aria-selected={tab === t} className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}>{{ inspect: "Inspector", copilot: "Copilot", run: "Run", tests: "Tests", versions: "Versions", code: "Code" }[t]}</button>
          ))}
        </nav>
        <ErrorBoundary key={tab} label="This panel">
          {tab === "inspect" && <Inspector />}
          {tab === "copilot" && <CopilotPanel />}
          {tab === "run" && <RunPanel />}
          {tab === "tests" && <TestsPanel />}
          {tab === "versions" && <VersionsPanel />}
          {tab === "code" && <CodePanel />}
        </ErrorBoundary>
      </aside>
      <footer className="dock">
        <nav className="tabs dock-tabs" role="tablist">
          {(["problems", "timeline", "explain"] as DockTab[]).map((t) => (
            <button type="button" role="tab" key={t} aria-selected={dock === t} className={dock === t ? "active" : ""}
              onClick={() => setDock(t)}>{{ problems: "Problems", timeline: "Timeline", explain: "Explain" }[t]}</button>
          ))}
        </nav>
        <ErrorBoundary key={dock} label="This panel">
          {dock === "problems" && <ProblemsPanel />}
          {dock === "timeline" && <Timeline />}
          {dock === "explain" && <ExplainView />}
        </ErrorBoundary>
      </footer>
      <CommandPalette onRun={() => setTab("run")} />
      <Tutor />
      <HelpDrawer />
    </div>
  );
}
