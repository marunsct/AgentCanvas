/** The Runs page's Ask box (F-11): a question about the project's runs ("failed runs about refunds last week over
 * $0.10"), answered by the Copilot with its run search. The model only picks the filters; the runs listed come from the
 * server's search. Nothing is kept. */
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { RunAskOut } from "../api/generated";
import { PROJECT_ID } from "../api/queries";
import { Badge, type Tone } from "../ui/Badge";
import { ago, exact } from "../ui/time";

const STATUS: Record<string, { label: string; tone: Tone }> = {
  success: { label: "done", tone: "ok" },
  running: { label: "running", tone: "running" },
  interrupted: { label: "waiting for a person", tone: "warn" },
  error: { label: "failed", tone: "err" },
  cancelled: { label: "stopped", tone: "neutral" },
};
const SHOWN = 20;

/** One search's filters in words: "failed, since 2026-11-03, over $0.10, about "refunds" (2 matched)". */
export function searchText(s: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof s.status === "string") parts.push(STATUS[s.status]?.label ?? s.status);
  if (typeof s.workflow === "string") parts.push(`workflow ${s.workflow}`);
  if (typeof s.since === "string") parts.push(`since ${s.since.slice(0, 16).replace("T", " ")}`);
  if (typeof s.until === "string") parts.push(`before ${s.until.slice(0, 16).replace("T", " ")}`);
  if (typeof s.min_cost_usd === "number") parts.push(`at least $${s.min_cost_usd}`);
  if (typeof s.max_cost_usd === "number") parts.push(`at most $${s.max_cost_usd}`);
  if (typeof s.label === "string") parts.push(`label ${s.label}`);
  if (typeof s.text === "string") parts.push(`about "${s.text}"`);
  const what = parts.length ? parts.join(", ") : "every run";
  return typeof s.matched === "number" ? `${what} (${s.matched} matched)` : what;
}

export function RunAsk({ workflowId }: { workflowId?: string }) {
  const [draft, setDraft] = useState("");
  const ask = useMutation({
    mutationFn: (question: string) => apiFetch<RunAskOut>(`/projects/${PROJECT_ID}/runs/ask`, {
      method: "POST", json: workflowId ? { question, workflow_id: workflowId } : { question },
    }),
  });
  const out = ask.data;
  const runs = Array.isArray(out?.runs) ? out.runs : [];
  return (
    <section className="run-search run-ask" aria-label="ask about runs">
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) ask.mutate(draft.trim()); }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} style={{ flex: 1, minWidth: 260 }} maxLength={1000}
          aria-label="ask the Copilot about runs" placeholder="Ask the Copilot… (e.g. failed runs about refunds last week over $0.10)" />
        <button type="submit" disabled={!draft.trim() || ask.isPending}>{ask.isPending ? "Asking…" : "Ask"}</button>
        {(out || ask.error) && <button type="button" className="link" onClick={() => { ask.reset(); setDraft(""); }}>Clear</button>}
      </form>
      {ask.error && <p className="error-text small" role="alert">{(ask.error as Error).message}</p>}
      {out && (
        <div data-testid="run-ask-answer">
          {out.answer && <p className="run-ask-text">{out.answer}</p>}
          {out.error && <p className="error-text small" role="alert">{out.error}</p>}
          {Array.isArray(out.searches) && out.searches.length > 0 && (
            <p className="muted small">Searched: {out.searches.map(searchText).join("; ")}.{out.cost_usd ? ` This answer cost $${out.cost_usd.toFixed(4)}.` : ""}</p>
          )}
          {runs.length > 0 && (
            <ul className="run-hits" aria-label="runs found">
              {runs.slice(0, SHOWN).map((r) => {
                const s = STATUS[r.status] ?? { label: r.status, tone: "neutral" as Tone };
                return (
                  <li key={r.run_id}>
                    <div className="row gap wrap">
                      <Badge tone={s.tone}>{s.label}</Badge>
                      <Link to={`/workflows/${r.workflow_id}?thread=${r.thread_id}`} title="Open in the editor's timeline">{r.workflow_name ?? r.workflow_id}</Link>
                      {r.started_at ? <small className="muted" title={exact(r.started_at)}>{ago(r.started_at)}</small> : null}
                      <small className="muted">${r.cost_usd.toFixed(4)}</small>
                    </div>
                    {r.error && <div className="error-text small" title={r.error}>{r.error.slice(0, 160)}</div>}
                  </li>
                );
              })}
            </ul>
          )}
          {runs.length > SHOWN && <p className="muted small">and {runs.length - SHOWN} more.</p>}
        </div>
      )}
    </section>
  );
}
