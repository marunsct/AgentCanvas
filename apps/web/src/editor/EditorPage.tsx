/** The workflow editor: palette | canvas | inspector/run/code, with the problems dock below. */
import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useInbox, useNodeTypes } from "../api/queries";
import { Canvas } from "../canvas/Canvas";
import { Palette } from "../canvas/Palette";
import { CodePanel } from "../code/CodePanel";
import { CopilotPanel } from "../copilot/CopilotPanel";
import { stopCopilotStream, useCopilot } from "../copilot/copilot";
import { Inspector } from "../inspector/Inspector";
import { ProblemsPanel } from "../problems/ProblemsPanel";
import { TestsPanel } from "../release/TestsPanel";
import { VersionsPanel } from "../release/VersionsPanel";
import { ExplainView } from "../run/ExplainView";
import { RunPanel } from "../run/RunPanel";
import { useRun } from "../run/runStore";
import { stopStreaming } from "../run/session";
import { Timeline } from "../run/Timeline";
import { ErrorBoundary, QueryStatus } from "../ui/QueryStatus";
import { Tutor } from "../tutor/Tutor";
import { CommandPalette } from "./CommandPalette";
import { runErrorHelp } from "../help/diagnostics";
import { HelpText } from "../help/HelpText";
import { FrameworkBadges } from "./Frameworks";
import { MoreMenu } from "./MoreMenu";
import { HelpDrawer, useFirstVisitHelp } from "../help/HelpDrawer";
import { useEditor } from "./store";
import { recallView } from "./viewMemory";
import { useCan, useMe } from "../api/session";
import { type DockTab, useUi } from "./ui";
import { SIMPLE_DOCK, SIMPLE_TABS } from "./simple";
import { PersonMenu } from "../app/AppShell";

type Tab = "inspect" | "copilot" | "run" | "tests" | "versions" | "code";

export function EditorPage() {
  const canBuild = useCan("builder");
  const role = useMe((s) => s.me.role);
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
  const simple = useUi((s) => s.simple);
/* oxlint-disable react-hooks/exhaustive-deps -- only switching to simple mode moves the open tab and dock */
  useEffect(() => {  // Simple mode hides some panels: leave them for one it shows
    if (!simple) return;
    if (!SIMPLE_TABS.has(tab)) setTab("inspect");
    if (!SIMPLE_DOCK.has(dock)) setDock("problems");
  }, [simple]);
/* oxlint-enable react-hooks/exhaustive-deps */
  const inbox = useInbox();
  const pendingDecisions = inbox.data?.length ?? 0;
  useFirstVisitHelp();
  const copilotRequest = useCopilot((s) => s.request);
  useEffect(() => { if (copilotRequest) setTab("copilot"); }, [copilotRequest]);

  useEffect(() => () => { stopStreaming(); stopCopilotStream(); }, []); // leaving the editor: stop reading streams

/* oxlint-disable react-hooks/exhaustive-deps -- runs when the workflow id changes, not on every search-param change */
  useEffect(() => {
    if (!id) return;
    stopStreaming(); // the last workflow's run and Copilot streams
    stopCopilotStream();
    useEditor.getState().reset();
    useRun.getState().setThread(null);
    useRun.setState({ runId: null, running: false });
    useCopilot.setState({ workflowId: null, conversation: null, ghost: null, error: null, busy: false, live: null });
    const sel = recallView(id)?.selected; // back from the Workspace: the block you had selected (S17)
    useEditor.getState().load(id)
      .then(() => {
        const st = useEditor.getState();
        if (sel && (st.wf?.nodes.some((n) => n.id === sel) || st.wf?.resources.some((r) => r.id === sel))) st.select(sel);
      })
      .catch((e: Error) => setError(e.message));
    const thread = params.get("thread");  // "open this run's timeline", e.g. from an experiment's results
    if (thread) {
      useRun.getState().setThread(thread);
      useUi.getState().setDock("timeline");
    }
  }, [id]);
/* oxlint-enable react-hooks/exhaustive-deps */

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
        {canBuild
          ? <span className={`save-state${saving ? " saving" : ""}`}>{saving ? "Saving…" : "Saved"}</span>
          : <span className="save-state" data-testid="view-only" title={`your role is ${role}`}>View only</span>}
        <FrameworkBadges />
        <span className="spacer" />
        <div className="toolbar-group" role="group" aria-label="Editor mode">
          <button type="button" className={simple ? "active" : ""} aria-pressed={simple} data-testid="simple-toggle"
            onClick={() => useUi.getState().setSimple(true)}
            title="Only the blocks and panels a first workflow needs">Simple</button>
          <button type="button" className={simple ? "" : "active"} aria-pressed={!simple} data-testid="full-toggle"
            onClick={() => useUi.getState().setSimple(false)} title="Every block, panel and tool">Full</button>
        </div>
        <Link to="/inbox" className="inbox-link" data-testid="inbox-link">
          Inbox{pendingDecisions > 0 && <span className="badge warn">{pendingDecisions}</span>}
        </Link>
        <div className="toolbar-group" role="group" aria-label="Canvas tools">
          <button type="button" onClick={() => useUi.getState().openPalette()} title="Command palette (Ctrl/⌘+K)">⌘K</button>
          <button type="button" onClick={() => void useEditor.getState().tidy?.()} title="Auto-layout (ELK)">Tidy</button>
        </div>
        {!simple && <MoreMenu wfId={wf.id} blast={blast} />}
        <div className="toolbar-group" role="group" aria-label="History">
          <button type="button" onClick={() => useEditor.getState().undo()} title="Undo (Ctrl/⌘+Z)" aria-label="Undo">↶</button>
          <button type="button" onClick={() => useEditor.getState().redo()} title="Redo (Ctrl/⌘+Shift+Z)" aria-label="Redo">↷</button>
        </div>
        <button type="button" onClick={() => useUi.getState().setHelp(!useUi.getState().help)} data-testid="help-button"
          title="A short guide for beginners: what the blocks, dots and arrows mean">❓ Help</button>
        <button type="button" onClick={() => setTab("copilot")} data-testid="copilot-button"
          title="Chat with the AI Copilot: describe a workflow, ask questions, get changes to review">✦ Copilot</button>
        <button type="button" className="primary" onClick={() => setTab("run")}>▶ Test</button>
        <PersonMenu />
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
          {(["inspect", "copilot", "run", "tests", "versions", "code"] as Tab[]).filter((t) => !simple || SIMPLE_TABS.has(t)).map((t) => (
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
          {(["problems", "timeline", "explain"] as DockTab[]).filter((t) => !simple || SIMPLE_DOCK.has(t)).map((t) => (
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
