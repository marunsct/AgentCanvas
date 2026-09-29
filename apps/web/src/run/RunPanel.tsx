/** Test-run panel (M4-T1): JSON input or chat, debug runs with breakpoints, approvals, continue/stop. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { useEditor } from "../editor/store";
import { DecisionCard } from "../inbox/DecisionCard";
import type { WF } from "../ir/model";
import { formatMetrics, useRun } from "./runStore";
import { testCaseFromRun } from "../release/testcases";
import { cancelRun, continueRun, debugAids, resumeRun, startRun } from "./session";

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

const EMPTY: Record<string, unknown> = { string: "", array: [], object: {}, number: 0, integer: 0, boolean: false };

/** A starter input: one key per input channel, typed (`topics: []`, `question: ""`). */
export function sampleInput(wf: WF): string {
  const inputs = wf.state.channels.filter((c) => (c.io ?? "inout") === "inout" || c.io === "in");
  const fields = inputs.filter((c) => c.reducer !== "add_messages").slice(0, 4);
  const empty = (c: (typeof fields)[number]) => EMPTY[(c.type as { type?: string } | undefined)?.type ?? "string"] ?? "";
  return JSON.stringify(Object.fromEntries(fields.map((c) => [c.name, empty(c)])), null, 2);
}

export function RunPanel() {
  const wf = useEditor((s) => s.wf);
  const errors = useEditor((s) => s.diagnostics.filter((d) => d.severity === "error").length);
  const busyEditing = useEditor((s) => s.pending.length > 0 || s.inflight);
  const breakpoints = useEditor((s) => s.breakpoints.length);
  const pinned = useEditor((s) => Object.keys(s.pins).length);
  const mocked = useEditor((s) => Object.keys(s.mocks).length);
  const run = useRun();
  const [chatOverride, setChatOverride] = useState<boolean | null>(null);
  if (!wf) return null;
  const blocked = run.running || errors > 0;
  const chat = chatOverride ?? isChat(wf);

  return (
    <div className="panel-body run-panel">
      <h3>
        Test run{" "}
        {(breakpoints > 0 || pinned > 0 || mocked > 0) && (
          <small className="badge warn" data-testid="debug-badge">
            debug{breakpoints > 0 && ` · ${breakpoints} breakpoint(s)`}{pinned > 0 && ` · ${pinned} pinned`}{mocked > 0 && ` · ${mocked} mocked`}
          </small>
        )}
      </h3>
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
      {run.usage && (run.usage.input_tokens + run.usage.output_tokens > 0 || run.durationMs) && (
        <p className="muted small" data-testid="run-usage">
          Run: {formatMetrics({
            tokens: run.usage.input_tokens + run.usage.output_tokens, cost: run.usage.cost_usd, priced: run.usage.priced,
            ms: run.durationMs ?? undefined,
          })}
          {!run.usage.priced && <span title="a model has no catalog price"> (cost incomplete)</span>}
        </p>
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
      <SaveAsTest />
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

/** "Save as test case" (M6-T9): this input must keep producing this status, route and output. */
function SaveAsTest() {
  const wf = useEditor((s) => s.wf);
  const run = useRun();
  const qc = useQueryClient();
  const [saved, setSaved] = useState<string | null>(null);
  if (!wf) return null;
  const draft = testCaseFromRun(wf, run, debugAids(), "");
  if (!draft) return null;
  const save = async () => {
    const name = window.prompt("Test case name", `case ${new Date().toLocaleTimeString()}`);
    if (!name) return;
    await apiFetch(`/workflows/${wf.id}/test-cases`, { method: "POST", json: { ...draft, name } });
    void qc.invalidateQueries({ queryKey: ["test-cases", wf.id] });
    setSaved(name);
  };
  return (
    <p className="row gap">
      <button type="button" onClick={() => void save()}>Save as test case</button>
      {saved && <span className="muted small" role="status">saved “{saved}”</span>}
    </p>
  );
}
