/** Timeline (M4-T5…T7): one tick per checkpoint; inspect state, edit it, or fork a new branch from it. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { useEditor } from "../editor/store";
import { useRun } from "./runStore";
import { editState, forkRun } from "./session";

export interface Checkpoint {
  checkpoint_id: string;
  parent_checkpoint_id: string | null;
  step: number | null;
  source: string | null;
  next: string[];
  writes: Record<string, unknown> | null;
  created_at: string | null;
  values?: Record<string, unknown>;
}

export function Timeline() {
  const wf = useEditor((s) => s.wf);
  const threadId = useRun((s) => s.threadId);
  const runSeq = useRun((s) => s.runSeq);
  const running = useRun((s) => s.running);
  const setHighlight = useRun((s) => s.setHighlight);
  const history = useQuery({
    queryKey: ["history", threadId, runSeq],
    queryFn: () => apiFetch<Checkpoint[]>(`/threads/${threadId}/history?include_values=true&limit=200`),
    enabled: Boolean(threadId),
  });
  const ticks = useMemo(() => [...(history.data ?? [])].reverse(), [history.data]);
  const [selected, setSelected] = useState<string | null>(null);
  const index = ticks.findIndex((t) => t.checkpoint_id === selected);
  const cp = index >= 0 ? ticks[index] : undefined;
  const [draft, setDraft] = useState("");
  useEffect(() => setDraft(cp ? JSON.stringify(cp.values ?? {}, null, 2) : ""), [cp]);
  useEffect(() => () => setHighlight([]), [setHighlight]);

  if (!threadId) return <p className="muted pad">Run the workflow to see its timeline.</p>;
  const name = (id: string) => wf?.nodes.find((n) => n.id === id)?.name ?? id;
  // the node whose step produced this checkpoint: the previous checkpoint's single `next` node
  const producer = index > 0 && ticks[index - 1].next.length === 1 ? ticks[index - 1].next[0] : undefined;
  let parsed: Record<string, unknown> | null = null;
  try {
    parsed = draft ? (JSON.parse(draft) as Record<string, unknown>) : null;
  } catch {
    parsed = null;
  }
  const changed = (): Record<string, unknown> | undefined => {
    if (!parsed || !cp?.values) return undefined;
    const out = Object.fromEntries(Object.entries(parsed).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(cp.values?.[k])));
    return Object.keys(out).length ? out : undefined;
  };

  return (
    <div className="timeline" data-testid="timeline">
      <div className="ticks" role="listbox" aria-label="checkpoints">
        {ticks.map((t, i) => (
          <button type="button" role="option" key={t.checkpoint_id} aria-selected={t.checkpoint_id === selected}
            className={`tick${t.checkpoint_id === selected ? " active" : ""}`}
            title={`${t.source ?? ""} · ${t.created_at ?? ""}`}
            onClick={() => { setSelected(t.checkpoint_id); setHighlight(t.next); }}>
            <b>{i}</b>
            <span>{t.next.length ? `→ ${t.next.map(name).join(", ")}` : "end"}</span>
          </button>
        ))}
        {history.isFetching && <span className="muted">…</span>}
      </div>
      {cp && (
        <div className="checkpoint">
          <div className="row gap">
            <small className="muted">checkpoint {cp.checkpoint_id.slice(0, 13)}… · step {cp.step} · {cp.source}</small>
            <span className="spacer" />
            <button type="button" disabled={running || !parsed || !changed()}
              onClick={() => void editState(cp.checkpoint_id, changed() ?? {}, producer)}>Save edit</button>
            <button type="button" className="primary" disabled={running || (draft !== "" && !parsed)}
              onClick={() => void forkRun(cp.checkpoint_id, { values: changed(), asNode: changed() ? producer : undefined })}>
              ⑂ Fork from here
            </button>
            <button type="button" disabled={running || (draft !== "" && !parsed)} title="continue in a new thread; this one stays unchanged"
              onClick={() => void forkRun(cp.checkpoint_id, { values: changed(), asNode: changed() ? producer : undefined, copy: true })}>
              Fork to new thread
            </button>
          </div>
          <textarea className={`mono${draft && !parsed ? " invalid" : ""}`} rows={5} value={draft}
            onChange={(e) => setDraft(e.target.value)} aria-label="checkpoint state" />
        </div>
      )}
    </div>
  );
}
