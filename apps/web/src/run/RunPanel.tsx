/** Test-run panel: input, run, live status, approvals (Sprint 4 extends it with chat, timeline, state). */
import { useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { useEditor } from "../editor/store";
import type { InterruptInfo } from "./events";
import { useRun } from "./runStore";
import { postStream } from "./sse";

function sampleInput(channels: { name: string; io?: string | null; reducer?: string | null }[]): string {
  const inputs = channels.filter((c) => (c.io ?? "inout") === "inout" || c.io === "in");
  const first = inputs.find((c) => c.reducer !== "add_messages");
  return JSON.stringify(first ? { [first.name]: "" } : {}, null, 2);
}

export function RunPanel() {
  const wf = useEditor((s) => s.wf);
  const diagnostics = useEditor((s) => s.diagnostics);
  const pending = useEditor((s) => s.pending.length + (s.inflight ? 1 : 0));
  const run = useRun();
  const initial = useMemo(() => sampleInput(wf?.state.channels ?? []), [wf?.id]);
  const [input, setInput] = useState(initial);
  const [newThread, setNewThread] = useState(true);
  const errors = diagnostics.filter((d) => d.severity === "error").length;
  if (!wf) return null;

  const stream = async (path: string, body: unknown) => {
    try {
      await postStream(path, body, run.apply);
    } catch (e) {
      const detail = e instanceof ApiError ? e.message : String(e);
      run.apply({ type: "RUN_ERROR", message: detail });
    }
  };
  const start = async () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(input || "{}");
    } catch {
      run.apply({ type: "RUN_ERROR", message: "input is not valid JSON" });
      return;
    }
    await useEditor.getState().flush();
    run.reset();
    await stream(`/workflows/${wf.id}/runs/stream`, { input: parsed, thread_id: newThread ? undefined : run.threadId ?? undefined });
  };
  const decide = (intr: InterruptInfo, value: unknown) =>
    stream(`/threads/${run.threadId}/resume`, { value, interrupt_id: intr.id });

  return (
    <div className="panel-body run-panel">
      <h3>Test run</h3>
      <label>
        Input (JSON)
        <textarea className="mono" rows={5} value={input} onChange={(e) => setInput(e.target.value)} aria-label="run input" />
      </label>
      <label className="row">
        <input type="checkbox" checked={!newThread} disabled={!run.threadId} onChange={(e) => setNewThread(!e.target.checked)} />
        continue thread {run.threadId ?? ""}
      </label>
      <button type="button" className="primary" onClick={() => void start()} disabled={run.running || errors > 0 || pending > 0}
        title={errors ? "fix the errors in the Problems panel first" : undefined}>
        {run.running ? "Running…" : "▶ Run"}
      </button>
      {errors > 0 && <p className="muted">{errors} error(s) block running — see Problems.</p>}
      {run.warnings.map((w, i) => <p key={i} className="warn-text">⚠ {w}</p>)}
      {run.error && <p className="error-text" role="alert">{run.error}</p>}
      {run.interrupts.map((intr) => <InterruptCard key={intr.id} intr={intr} onDecide={(v) => void decide(intr, v)} />)}
      {run.breakpoint && <p className="muted">Paused at a breakpoint.</p>}
      {Object.keys(run.output).length > 0 && (
        <>
          <h4>Streamed output</h4>
          {Object.entries(run.output).map(([id, text]) => (
            <p key={id} className="stream"><code>{wf.nodes.find((n) => n.id === id)?.name ?? id}</code> {text}</p>
          ))}
        </>
      )}
      {run.finalOutput && (
        <>
          <h4>Output</h4>
          <pre className="json" data-testid="run-output">{JSON.stringify(run.finalOutput, null, 2)}</pre>
        </>
      )}
    </div>
  );
}

function InterruptCard({ intr, onDecide }: { intr: InterruptInfo; onDecide: (v: unknown) => void }) {
  const payload = intr.payload as Record<string, unknown>;
  const tool = intr.kind === "tool_approval";
  const requests = tool ? (payload.action_requests as { name: string; args: unknown }[]) : [];
  return (
    <div className="interrupt-card" role="group" aria-label="Approval needed">
      <b>Approval needed</b> <small className="muted">{intr.kind}</small>
      {tool
        ? requests.map((r, i) => <pre key={i} className="json">{r.name}({JSON.stringify(r.args, null, 2)})</pre>)
        : <pre className="json">{JSON.stringify(payload.value ?? payload, null, 2)}</pre>}
      <div className="row gap">
        <button type="button" className="primary"
          onClick={() => onDecide(tool ? { decisions: requests.map(() => ({ type: "approve" })) } : { approved: true })}>Approve</button>
        <button type="button"
          onClick={() => onDecide(tool ? { decisions: requests.map(() => ({ type: "reject", message: "Rejected by reviewer" })) }
            : { approved: false })}>Reject</button>
      </div>
    </div>
  );
}
