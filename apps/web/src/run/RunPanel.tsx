/** Test-run panel (M4-T1): JSON input or chat, debug runs with breakpoints, approvals, continue/stop. */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { useEditor } from "../editor/store";
import { DecisionCard } from "../inbox/DecisionCard";
import type { WF } from "../ir/model";
import { useRun } from "./runStore";
import { cancelRun, continueRun, resumeRun, startRun } from "./session";

interface ChatMessage {
  type?: string;
  content?: unknown;
  tool_calls?: { name: string; args: unknown }[];
  name?: string;
}

/** Chat mode when `messages` (add_messages) is the only input channel; otherwise a JSON/form input. */
export function isChat(wf: WF): boolean {
  const inputs = wf.state.channels.filter((c) => (c.io ?? "inout") === "inout" || c.io === "in");
  const hasMessages = inputs.some((c) => c.name === "messages" && c.reducer === "add_messages");
  return hasMessages && inputs.every((c) => c.name === "messages");
}

function sampleInput(wf: WF): string {
  const inputs = wf.state.channels.filter((c) => (c.io ?? "inout") === "inout" || c.io === "in");
  const fields = inputs.filter((c) => c.reducer !== "add_messages").slice(0, 3);
  return JSON.stringify(Object.fromEntries(fields.map((c) => [c.name, ""])), null, 2);
}

export function RunPanel() {
  const wf = useEditor((s) => s.wf);
  const errors = useEditor((s) => s.diagnostics.filter((d) => d.severity === "error").length);
  const busyEditing = useEditor((s) => s.pending.length > 0 || s.inflight);
  const breakpoints = useEditor((s) => s.breakpoints.length);
  const run = useRun();
  const [chatOverride, setChatOverride] = useState<boolean | null>(null);
  if (!wf) return null;
  const blocked = run.running || errors > 0;
  const chat = chatOverride ?? isChat(wf);

  return (
    <div className="panel-body run-panel">
      <h3>Test run {breakpoints > 0 && <small className="badge warn">debug · {breakpoints} breakpoint(s)</small>}</h3>
      <div className="row gap mode-toggle" role="radiogroup" aria-label="input mode">
        <button type="button" role="radio" aria-checked={!chat} className={!chat ? "active" : ""} onClick={() => setChatOverride(false)}>JSON</button>
        <button type="button" role="radio" aria-checked={chat} className={chat ? "active" : ""} onClick={() => setChatOverride(true)}>Chat</button>
      </div>
      {chat ? <ChatInput wf={wf} disabled={blocked} /> : <FormInput wf={wf} disabled={blocked || busyEditing} />}
      {errors > 0 && <p className="muted">{errors} error(s) block running — see Problems.</p>}
      <div className="row gap">
        {run.running && <button type="button" onClick={() => void cancelRun()}>■ Stop</button>}
        {run.breakpoint && !run.running && (
          <button type="button" className="primary" onClick={() => void continueRun()}>⏵ Continue</button>
        )}
      </div>
      {run.breakpoint && !run.running && (
        <p className="muted">Paused at a breakpoint before {run.next.map((id) => wf.nodes.find((n) => n.id === id)?.name ?? id).join(", ")}.</p>
      )}
      {run.warnings.map((w, i) => <p key={i} className="warn-text">⚠ {w}</p>)}
      {run.error && <p className="error-text" role="alert">{run.error}</p>}
      {!run.running && run.interrupts.map((intr) => (
        <DecisionCard key={intr.id} intr={intr} onDecide={(v) => void resumeRun(v, intr.id)} />
      ))}
      {Object.keys(run.output).length > 0 && !chat && (
        <>
          <h4>Streamed output</h4>
          {Object.entries(run.output).map(([id, text]) => (
            <p key={id} className="stream"><code>{wf.nodes.find((n) => n.id === id)?.name ?? id}</code> {text}</p>
          ))}
        </>
      )}
      {run.finalOutput && !chat && (
        <>
          <h4>Output</h4>
          <pre className="json" data-testid="run-output">{JSON.stringify(run.finalOutput, null, 2)}</pre>
        </>
      )}
    </div>
  );
}

function FormInput({ wf, disabled }: { wf: WF; disabled: boolean }) {
  const initial = useMemo(() => sampleInput(wf), [wf]);
  const [input, setInput] = useState(initial);
  const [cont, setCont] = useState(false);
  const run = useRun();
  const start = () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(input || "{}");
    } catch {
      run.apply({ type: "RUN_ERROR", message: "input is not valid JSON" });
      return;
    }
    void startRun(parsed, { continueThread: cont });
  };
  return (
    <>
      <label>
        Input (JSON)
        <textarea className="mono" rows={5} value={input} onChange={(e) => setInput(e.target.value)} aria-label="run input" />
      </label>
      <label className="row">
        <input type="checkbox" checked={cont} disabled={!run.threadId} onChange={(e) => setCont(e.target.checked)} />
        continue thread <code>{run.threadId ?? "—"}</code>
      </label>
      <button type="button" className="primary" onClick={start} disabled={disabled}>{run.running ? "Running…" : "▶ Run"}</button>
    </>
  );
}

function ChatInput({ wf, disabled }: { wf: WF; disabled: boolean }) {
  const run = useRun();
  const [text, setText] = useState("");
  const thread = useQuery({
    queryKey: ["thread", run.threadId, run.runSeq],
    queryFn: () => apiFetch<{ values: { messages?: ChatMessage[] } }>(`/threads/${run.threadId}`),
    enabled: Boolean(run.threadId),
  });
  const messages = thread.data?.values.messages ?? [];
  const send = () => {
    if (!text.trim()) return;
    const content = text.trim();
    setText("");
    void startRun({ messages: [{ role: "user", content }] }, { continueThread: true });
  };
  return (
    <div className="chat" data-testid="chat">
      <div className="chat-log">
        {messages.length === 0 && <p className="muted">Send a message to start a conversation with “{wf.name}”.</p>}
        {messages.map((m, i) => <ChatBubble key={i} m={m} />)}
        {run.running && Object.values(run.output).map((t, i) => <div key={`s${i}`} className="bubble ai streaming">{t}</div>)}
      </div>
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message…" aria-label="chat message" disabled={disabled} />
        <button type="submit" className="primary" disabled={disabled}>Send</button>
      </form>
      <button type="button" className="link small" onClick={() => useRun.getState().setThread(null)}>New conversation</button>
    </div>
  );
}

function ChatBubble({ m }: { m: ChatMessage }) {
  const role = m.type === "human" ? "human" : m.type === "tool" ? "tool" : "ai";
  const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
  return (
    <div className={`bubble ${role}`}>
      {m.tool_calls?.map((c, i) => <code key={i} className="tool-call">🔧 {c.name}({JSON.stringify(c.args)})</code>)}
      {role === "tool" && <small className="muted">{m.name} → </small>}
      {content}
    </div>
  );
}
