/** The Copilot panel (S14): chat about the open workflow; its changes appear on the canvas to accept or reject. */
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { can, useCan, useMe } from "../api/session";
import { useEditor } from "../editor/store";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { apiFetch } from "../api/client";
import type { CopilotThreadOut } from "../api/generated";
import { confirmDialog } from "../ui/dialog";
import { Markdown } from "../ui/Markdown";
import { Menu } from "../ui/Menu";
import { type Brief, type Mode, type Proposal, type Selection, type Todo, toolLabel, useCopilot } from "./copilot";
import { describe, preview } from "./review";

const STARTERS = [
  "Build a workflow that classifies support emails and drafts a reply",
  "Explain what this workflow does",
  "Why did the last run fail?",
  "Fix the problems in this workflow",
  "Write tests for this workflow and run them",
];

function BriefCard({ brief }: { brief: Brief }) {
  const rows: [string, string[] | undefined][] = [
    ["Inputs", brief.inputs], ["Outputs", brief.outputs], ["Steps", brief.steps], ["Tools", brief.tools],
    ["Works when", brief.success_criteria], ["Open questions", brief.open_questions],
  ];
  return (
    <section className="copilot-brief" data-testid="copilot-brief" aria-label="Brief">
      <h4>Brief</h4>
      <p>{brief.goal}</p>
      {rows.filter(([, v]) => v?.length).map(([k, v]) => (
        <div key={k}><span className="muted small">{k}</span><ul>{v!.map((x) => <li key={x}>{x}</li>)}</ul></div>
      ))}
    </section>
  );
}

/** The person's earlier conversations about this workflow: reopen one, or delete it. */
function Threads({ workflowId, current, busy }: { workflowId: string | null; current: string | null; busy: boolean }) {
  const threads = useQuery({
    queryKey: ["copilot-threads", workflowId, current],
    queryFn: () => apiFetch<CopilotThreadOut[]>(`/workflows/${workflowId}/copilot/threads`),
    enabled: Boolean(workflowId),
  });
  const others = (threads.data ?? []).filter((t) => !t.open);
  return (
    <Menu label={<><History size={14} aria-hidden /> History</>} testId="copilot-history" title="Earlier conversations about this workflow">
      {(close) => (
        <>
          <p className="menu-heading">Earlier conversations</p>
          {threads.isError && <p className="error-text small">Could not list them: {(threads.error as Error).message}</p>}
          {threads.data && others.length === 0 && <p className="muted small" style={{ margin: "4px 10px" }}>None yet. New chat keeps this one here.</p>}
          {others.map((t) => (
            <div key={t.id} className="thread-row">
              <button type="button" role="menuitem" disabled={busy} title={t.title ?? "Untitled"}
                onClick={() => { close(); void useCopilot.getState().openThread(t.id); }}>
                <span className="thread-title">{t.title ?? "Untitled"}</span>
                <small className="muted">{new Date(t.updated_at).toLocaleDateString()} · {t.messages} messages</small>
              </button>
              <button type="button" className="link small" aria-label={`delete conversation ${t.title ?? ""}`} disabled={busy}
                onClick={async () => {
                  close();
                  if (await confirmDialog({ title: "Delete this conversation?", body: t.title ?? undefined, confirm: "Delete", danger: true })) {
                    await useCopilot.getState().deleteThread(t.id);
                    void threads.refetch();
                  }
                }}>×</button>
            </div>
          ))}
        </>
      )}
    </Menu>
  );
}

function Plan({ todos }: { todos: Todo[] }) {
  const done = todos.filter((t) => t.status === "completed").length;
  return (
    <div className="copilot-plan" data-testid="copilot-plan" aria-label="The Copilot's plan">
      <div className="small muted">Plan · {done} of {todos.length} done</div>
      <ul>
        {todos.map((t, i) => (
          <li key={i} className={t.status}>
            <span aria-hidden="true">{t.status === "completed" ? "✓" : t.status === "in_progress" ? "▸" : "○"}</span>
            <span>{t.content}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The open proposal: each change with a tick box (unticking a step unticks the wires to it), its tests and notes,
 * and Accept for what is ticked. The canvas shows the ticked changes as they would be. */
function ProposalCard({ pending, busy, canBuild }: { pending: Proposal; busy: boolean; canBuild: boolean }) {
  const selection = useCopilot((s) => s.selection);
  const wf = useEditor((s) => s.wf);
  const cmds = pending.commands ?? [];
  const named = wf && cmds.length ? preview(wf, cmds, new Set(cmds.keys())).workflow : null;
  const sel: Selection = selection ?? { changes: new Set(cmds.keys()), tests: new Set((pending.tests ?? []).keys()), notes: true };
  const kept = sel.changes.size + sel.tests.size + (sel.notes && pending.notes?.length ? 1 : 0);
  const partial = sel.changes.size < cmds.length || sel.tests.size < (pending.tests?.length ?? 0);
  const { toggleChange, toggleTest, toggleNotes } = useCopilot.getState();
  return (
    <section className="copilot-proposal" data-testid="copilot-proposal" aria-label="Proposed changes">
      <strong>Proposed: {pending.summary}</strong>
      {pending.stale
        ? <p className="warn-text small">{pending.problem ?? "The workflow changed since; ask again."}</p>
        : <p className="small muted">
            On the canvas: green is new, amber is changed, red dashed is removed.
            {pending.errors > 0 && ` It still has ${pending.errors} problem${pending.errors > 1 ? "s" : ""}.`}
          </p>}
      {!pending.stale && cmds.length > 0 && named && (
        <details className="change-list" open={cmds.length <= 8}>
          <summary className="small">{sel.changes.size} of {cmds.length} change{cmds.length > 1 ? "s" : ""} kept</summary>
          <ul>
            {cmds.map((c, i) => (
              <li key={i}>
                <label className="row">
                  <input type="checkbox" checked={sel.changes.has(i)} disabled={busy} onChange={() => toggleChange(i)} />
                  <span>{describe(c, named)}</span>
                </label>
              </li>
            ))}
          </ul>
        </details>
      )}
      {Boolean(pending.tests?.length) && (
        <div className="small" data-testid="copilot-proposal-tests">
          Saves tests:
          <ul className="plain">
            {pending.tests!.map((t, i) => (
              <li key={i}><label className="row"><input type="checkbox" checked={sel.tests.has(i)} disabled={busy} onChange={() => toggleTest(i)} /> {t.name}</label></li>
            ))}
          </ul>
        </div>
      )}
      {Boolean(pending.notes?.length) && (
        <label className="row small"><input type="checkbox" checked={sel.notes} disabled={busy} onChange={toggleNotes} />
          Add to project notes: {pending.notes!.join("; ")}</label>
      )}
      <div className="row gap">
        <button type="button" className="primary" disabled={busy || pending.stale || !canBuild || kept === 0}
          onClick={() => void useCopilot.getState().accept()}>{partial ? "Accept the ticked changes" : "Accept"}</button>
        <button type="button" disabled={busy} onClick={() => void useCopilot.getState().reject()}>Reject</button>
      </div>
      <p className="small muted">After accepting, Undo (Ctrl/⌘+Z) takes it back in one step.</p>
    </section>
  );
}

export function CopilotPanel() {
  const workflowId = useEditor((s) => s.workflowId);
  const { conversation, busy, live, error, last, cost } = useCopilot();
  const [text, setText] = useState("");
  const canBuild = useCan("builder");
  const email = useMe((s) => s.me.email);
  const [mode, setMode] = useState<Mode>(can("builder") ? "build" : "ask");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (workflowId) void useCopilot.getState().load(workflowId).catch((e: Error) => useCopilot.setState({ error: e.message }));
  }, [workflowId]);
  const request = useCopilot((s) => s.request);
  useEffect(() => { // "Ask the Copilot" buttons elsewhere in the editor
    if (!request || !conversation || busy) return;
    useCopilot.setState({ request: null });
    setMode(request.mode === "build" && !can("builder") ? "ask" : request.mode);
    void useCopilot.getState().send(request.text, request.mode === "build" && !can("builder") ? "ask" : request.mode);
  }, [request, conversation, busy]);
  useEffect(() => { // newer browsers return a promise here: an effect must not return it as its cleanup
    end.current?.scrollIntoView?.({ block: "end" });
  }, [conversation?.messages.length, live?.text.length, live?.activity.length, live?.streaming.length, live?.todos.length]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const t = text.trim();
    if (!t) return;
    setText("");
    void useCopilot.getState().send(t, mode);
  };

  if (!conversation) return <div className="panel copilot muted" role="status">{error ?? "Loading the Copilot…"}</div>;
  if (!conversation.enabled) return <div className="panel copilot muted">The Copilot is switched off for this project.</div>;
  const pending = conversation.pending;
  const used = (conversation.usage.input_tokens ?? 0) + (conversation.usage.output_tokens ?? 0);

  return (
    <div className="panel copilot" data-testid="copilot-panel">
      <div className="copilot-head row">
        <span className="copilot-title"><span className="copilot-mark" aria-hidden="true">✦</span>Copilot</span>
        <span className="model-chip" title={conversation.model.origin ? `${conversation.model.model} (${conversation.model.origin})` : undefined}>
          {conversation.model.model ?? "No model yet"}
        </span>
        <span className="spacer" />
        <Threads workflowId={workflowId} current={conversation.id} busy={busy} />
        <button type="button" className="link small" onClick={() => void useCopilot.getState().newChat()} disabled={busy}
          title="Start a new conversation; this one is kept under History">New chat</button>
      </div>
      {conversation.model.problem && (
        <p className="error-text small" data-testid="copilot-model-problem">
          {conversation.model.problem} <Link to="/settings/copilot">Copilot settings</Link>
        </p>
      )}
      {conversation.brief && <BriefCard brief={conversation.brief} />}

      <div className="copilot-log" aria-live="polite">
        {conversation.messages.length === 0 && !live && (
          <div className="copilot-empty">
            <div className="copilot-hero">
              <div className="copilot-mark" aria-hidden="true">✦</div>
              <h3>What are we building?</h3>
              <p className="muted small">Describe the workflow you want, or ask about this one. Changes show up on the canvas for you to accept.</p>
            </div>
            {STARTERS.map((s) => (
              <button type="button" key={s} className="chip" onClick={() => setText(s)}>{s}</button>
            ))}
          </div>
        )}
        {conversation.messages.map((m, i) => (
          <div key={i} className={`copilot-turn ${m.role}`}>
            <span className="copilot-avatar" aria-hidden="true">{m.role === "user" ? (email?.[0] ?? "Y").toUpperCase() : "✦"}</span>
            <div className={`copilot-msg ${m.role}`}>
              {m.role === "assistant"
                ? <Markdown text={m.content || (m.proposal_id ? "Proposed changes." : "")} />
                : m.content}
              {m.stopped && <span className="badge small" title="You stopped this answer">stopped</span>}
            </div>
          </div>
        ))}
        {live && (
          <div className="copilot-turn assistant">
            <span className="copilot-avatar" aria-hidden="true">✦</span>
            <div className="copilot-msg assistant live" data-testid="copilot-live">
              {live.activity.length > 0 && (
                <div className="copilot-activity">
                  {live.activity.map((a, i) => (
                    <div key={i} className={`small ${a.ok ? "muted" : "warn-text"}`}>
                      {a.ok ? "✓" : "↻"} {toolLabel(a.name)}{a.summary ? `: ${a.summary}` : ""}{!a.ok && a.error ? ` (fixing: ${a.error.slice(0, 120)})` : ""}
                    </div>
                  ))}
                </div>
              )}
              {live.todos.length > 0 && <Plan todos={live.todos} />}
              {live.text.map((t, i) => <Markdown key={i} text={t} />)}
              {live.streaming && <div className="streaming"><Markdown text={live.streaming} /></div>}
              {!live.streaming && <span className="typing" aria-label="Thinking"><i /><i /><i /> Thinking…</span>}
            </div>
          </div>
        )}
        <div ref={end} />
      </div>

      {pending && <ProposalCard pending={pending} busy={busy} canBuild={canBuild} />}
      {error && (
        <div className="copilot-error" role="alert" data-testid="copilot-error">
          <span>{error}</span>
          {last && !busy && <button type="button" className="small" onClick={() => void useCopilot.getState().retry()}>Try again</button>}
        </div>
      )}

      <form className="copilot-input" onSubmit={submit}>
        <textarea aria-label="Message the Copilot" rows={2} value={text} disabled={busy}
          placeholder={mode === "build" ? "What should this workflow do?" : "Ask about this workflow"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) submit(e); }} />
        <div className="row gap">
          <div className="segmented" role="radiogroup" aria-label="Copilot mode">
            {(["build", "ask"] as Mode[]).map((m) => (
              <button type="button" key={m} role="radio" aria-checked={mode === m} className={mode === m ? "active" : ""}
                disabled={m === "build" && !canBuild} onClick={() => setMode(m)}
                title={m === "build" ? "The Copilot may propose changes" : "Questions only; nothing changes"}>
                {m === "build" ? "Build" : "Ask"}
              </button>
            ))}
          </div>
          <span className="spacer" />
          <span className="small muted" title={`${used.toLocaleString()} tokens of ${conversation.token_cap.toLocaleString()} in this conversation`}>
            {cost != null && cost > 0 ? `$${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(2)} · ` : ""}{Math.round((used / conversation.token_cap) * 100)}% used</span>
          {busy
            ? <button type="button" data-testid="copilot-stop" onClick={() => useCopilot.getState().stop()} title="Stop this answer; what is said so far is kept">■ Stop</button>
            : <button type="submit" className="primary" disabled={!text.trim()}>Send <span aria-hidden="true">↑</span></button>}
        </div>
      </form>
    </div>
  );
}
