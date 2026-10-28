/** The Copilot panel (S14): chat about the open workflow; its changes appear on the canvas to accept or reject. */
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { can, useCan, useMe } from "../api/session";
import { useEditor } from "../editor/store";
import { Markdown } from "../ui/Markdown";
import { type Brief, type Mode, type Todo, toolLabel, useCopilot } from "./copilot";

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

export function CopilotPanel() {
  const workflowId = useEditor((s) => s.workflowId);
  const { conversation, busy, live, error, last } = useCopilot();
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
        <button type="button" className="link small" onClick={() => void useCopilot.getState().newChat()} disabled={busy}>New chat</button>
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

      {pending && (
        <section className="copilot-proposal" data-testid="copilot-proposal" aria-label="Proposed changes">
          <strong>Proposed: {pending.summary}</strong>
          {pending.stale
            ? <p className="warn-text small">{pending.problem ?? "The workflow changed since; ask again."}</p>
            : <p className="small muted">
                Shown on the canvas: {pending.diff?.added.length ?? 0} new, {pending.diff?.changed.length ?? 0} changed
                {pending.diff?.removed.length ? `, ${pending.diff.removed.length} removed` : ""}.
                {pending.errors > 0 && ` It still has ${pending.errors} problem${pending.errors > 1 ? "s" : ""}.`}
              </p>}
          {Boolean(pending.tests?.length) && (
            <p className="small" data-testid="copilot-proposal-tests">
              Saves {pending.tests!.length} test{pending.tests!.length > 1 ? "s" : ""}: {pending.tests!.map((t) => t.name).join(", ")}
            </p>
          )}
          {Boolean(pending.notes?.length) && <p className="small">Adds to project notes: {pending.notes!.join("; ")}</p>}
          <div className="row gap">
            <button type="button" className="primary" disabled={busy || pending.stale || !canBuild}
              onClick={() => void useCopilot.getState().accept()}>Accept</button>
            <button type="button" disabled={busy} onClick={() => void useCopilot.getState().reject()}>Reject</button>
          </div>
          <p className="small muted">After accepting, Undo (Ctrl/⌘+Z) takes it back in one step.</p>
        </section>
      )}
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
          <span className="small muted" title="tokens used in this conversation">{Math.round((used / conversation.token_cap) * 100)}% used</span>
          {busy
            ? <button type="button" data-testid="copilot-stop" onClick={() => useCopilot.getState().stop()} title="Stop this answer; what is said so far is kept">■ Stop</button>
            : <button type="submit" className="primary" disabled={!text.trim()}>Send <span aria-hidden="true">↑</span></button>}
        </div>
      </form>
    </div>
  );
}
