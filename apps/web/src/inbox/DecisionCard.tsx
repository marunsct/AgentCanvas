/** Decision card for a pending interrupt (build-guide/06 §9): tool approval, approval, or input request. */
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import { useState } from "react";

export interface PendingInterrupt {
  id: string;
  kind: "tool_approval" | "approval" | "input" | string;
  payload: unknown;
}

interface ActionRequest {
  name: string;
  args: Record<string, unknown>;
  description?: string;
}

function parseJson(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false };
  }
}

export function DecisionCard({ intr, onDecide, busy }: { intr: PendingInterrupt; onDecide: (value: unknown) => void; busy?: boolean }) {
  const payload = (intr.payload ?? {}) as Record<string, unknown>;
  return (
    <div className="interrupt-card" role="group" aria-label={intr.kind === "input" ? "Input needed" : "Approval needed"}>
      <b>{intr.kind === "input" ? "Input needed" : "Approval needed"}</b> <small className="muted">{intr.kind}</small>
      {intr.kind === "tool_approval"
        ? <ToolApproval requests={(payload.action_requests as ActionRequest[]) ?? []} onDecide={onDecide} busy={busy} />
        : intr.kind === "approval"
          ? <ValueApproval value={payload.value} onDecide={onDecide} busy={busy} />
          : <InputRequest payload={payload} onDecide={onDecide} busy={busy} />}
    </div>
  );
}

function ToolApproval({ requests, onDecide, busy }: { requests: ActionRequest[]; onDecide: (v: unknown) => void; busy?: boolean }) {
  const [drafts, setDrafts] = useState(() => requests.map((r) => JSON.stringify(r.args, null, 2)));
  const [message, setMessage] = useState("");
  const edited = requests.map((r, i) => drafts[i] !== JSON.stringify(r.args, null, 2));
  const parsed = drafts.map(parseJson);
  const valid = parsed.every((p) => p.ok);
  const approve = () =>
    onDecide({
      decisions: requests.map((r, i) => {
        const p = parsed[i];
        return edited[i] && p.ok ? { type: "edit", edited_action: { name: r.name, args: p.value } } : { type: "approve" };
      }),
    });
  return (
    <>
      {requests.map((r, i) => (
        <div key={i} className="action">
          <code>{r.name}</code>
          <textarea className={`mono${parsed[i].ok ? "" : " invalid"}`} rows={4} value={drafts[i]} aria-label={`arguments of ${r.name}`}
            onChange={(e) => setDrafts(drafts.map((d, j) => (j === i ? e.target.value : d)))} />
        </div>
      ))}
      <input placeholder="Reason (sent to the agent when rejecting)" value={message} onChange={(e) => setMessage(e.target.value)} />
      <div className="row gap">
        <button type="button" className="primary" disabled={busy || !valid} onClick={approve}>
          {edited.some(Boolean) ? "Approve with edits" : "Approve"}
        </button>
        <button type="button" disabled={busy}
          onClick={() => onDecide({ decisions: requests.map(() => ({ type: "reject", message: message || "Rejected by reviewer" })) })}>
          Reject
        </button>
      </div>
    </>
  );
}

function ValueApproval({ value, onDecide, busy }: { value: unknown; onDecide: (v: unknown) => void; busy?: boolean }) {
  const initial = JSON.stringify(value ?? null, null, 2);
  const [draft, setDraft] = useState(initial);
  const p = parseJson(draft);
  return (
    <>
      <textarea className={`mono${p.ok ? "" : " invalid"}`} rows={4} value={draft} aria-label="value to approve"
        onChange={(e) => setDraft(e.target.value)} />
      <div className="row gap">
        <button type="button" className="primary" disabled={busy || !p.ok}
          onClick={() => onDecide(draft !== initial && p.ok ? { approved: true, value: p.value } : { approved: true })}>
          {draft !== initial ? "Approve with edits" : "Approve"}
        </button>
        <button type="button" disabled={busy} onClick={() => onDecide({ approved: false })}>Reject</button>
      </div>
    </>
  );
}

function InputRequest({ payload, onDecide, busy }: { payload: Record<string, unknown>; onDecide: (v: unknown) => void; busy?: boolean }) {
  const schema = payload.response_schema as Record<string, unknown> | undefined;
  const [draft, setDraft] = useState("");
  const question = (payload.prompt ?? payload.question) as string | undefined;
  return (
    <>
      {question && <p>{question}</p>}
      {typeof payload.error === "string" && <p className="error-text" role="alert">Please try again: {payload.error}</p>}
      {schema ? (
        <Form schema={schema} validator={validator} onSubmit={(e) => onDecide(e.formData)} disabled={busy} />
      ) : (
        <>
          <pre className="json">{JSON.stringify(payload, null, 2)}</pre>
          <input placeholder="Your answer (text or JSON)" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="answer" />
          <button type="button" className="primary" disabled={busy}
            onClick={() => { const p = parseJson(draft); onDecide(p.ok ? p.value : draft); }}>Send answer</button>
        </>
      )}
    </>
  );
}
