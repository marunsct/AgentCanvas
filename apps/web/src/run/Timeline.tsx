/** Timeline (M4-T5…T7): one tick per checkpoint; inspect state, edit it, or fork a new branch from it. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import { useEditor } from "../editor/store";
import { lineageAt } from "./lineage";
import { useRun } from "./runStore";
import { editState, forkRun } from "./session";
import { QueryStatus } from "../ui/QueryStatus";
import type { ScoreOut } from "../api/generated";
import { ScoreBadge } from "../evaluation/ScoreBadge";
import { TrajectoryView } from "./TrajectoryView";

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
  const running = useRun((s) => s.running || s.starting); // starting: a fork was pressed and has not begun
  const setHighlight = useRun((s) => s.setHighlight);
  const history = useQuery({
    queryKey: ["history", threadId, runSeq],
    queryFn: () => apiFetch<Checkpoint[]>(`/threads/${threadId}/history?include_values=true&limit=200`),
    enabled: Boolean(threadId),
    // a run ending (runSeq) refetches: keep the ticks, and the checkpoint being edited, on screen meanwhile
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === threadId ? keepPreviousData(prev) : undefined),
  });
  const ticks = useMemo(() => [...(history.data ?? [])].reverse(), [history.data]);
  const [selected, setSelected] = useState<string | null>(null);
  const index = ticks.findIndex((t) => t.checkpoint_id === selected);
  const cp = index >= 0 ? ticks[index] : undefined;
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<"checkpoints" | "trajectory">("checkpoints");
  // keyed on the checkpoint, not the object: a refetch hands back a new one and used to wipe what was being typed
  const cpId = cp?.checkpoint_id;
  // oxlint-disable-next-line react-hooks/exhaustive-deps -- cpId stands for cp
  useEffect(() => setDraft(cp ? JSON.stringify(cp.values ?? {}, null, 2) : ""), [cpId]);
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

  const views = (
    <div className="toolbar-group" role="group" aria-label="Timeline view">
      {(["checkpoints", "trajectory"] as const).map((v) => (
        <button type="button" key={v} className={view === v ? "active" : ""} aria-pressed={view === v} onClick={() => setView(v)}
          title={v === "trajectory" ? "What each step, agent and helper did, as a tree with times" : "Every saved state: inspect, edit, fork"}>
          {v === "trajectory" ? "Trajectory" : "Checkpoints"}
        </button>
      ))}
    </div>
  );
  if (view === "trajectory") {
    return (
      <div className="timeline" data-testid="timeline">
        <div className="row gap"><ThreadScores threadId={threadId} runSeq={runSeq} /><span className="spacer" />{views}</div>
        <TrajectoryView threadId={threadId} runSeq={runSeq} onPick={(id) => setHighlight([id])} />
      </div>
    );
  }

  return (
    <div className="timeline" data-testid="timeline">
      <div className="row gap"><ThreadScores threadId={threadId} runSeq={runSeq} /><span className="spacer" />{views}</div>
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
        {history.isError && <QueryStatus q={history} what="the history" />}
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
          <Lineage ticks={ticks} index={index} name={name} onPick={(id) => setHighlight([id])} />
        </div>
      )}
    </div>
  );
}

/** "Written by" per channel at the selected checkpoint (FR-X-18); clicking a writer highlights it. */
function Lineage({ ticks, index, name, onPick }: {
  ticks: Checkpoint[]; index: number; name: (id: string) => string; onPick: (id: string) => void;
}) {
  const lineage = useMemo(() => lineageAt(ticks, index), [ticks, index]);
  const channels = Object.keys(lineage).sort();
  if (!channels.length) return null;
  return (
    <div className="lineage" aria-label="channel lineage">
      <small className="muted">Written by</small>
      <ul>
        {channels.map((ch) => {
          const w = lineage[ch];
          return (
            <li key={ch} data-testid={`lineage-${ch}`}>
              <code>{ch}</code> ←{" "}
              {w.writer === "input"
                ? <span>run input</span>
                : <button type="button" className="link" onClick={() => onPick(w.writer)}>{name(w.writer)}</button>}
              <small className="muted"> · step {w.step ?? "?"}</small>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Scores the thread's runs were given (online evaluators, S13): shown above the checkpoints when there are any. */
function ThreadScores({ threadId, runSeq }: { threadId: string; runSeq: number }) {
  const scores = useQuery({ queryKey: ["thread-scores", threadId, runSeq], queryFn: () => apiFetch<ScoreOut[]>(`/threads/${threadId}/scores`) });
  if (!scores.data?.length) return null;
  return (
    <div className="row gap small" data-testid="thread-scores">
      <span className="muted">Scores</span>
      {scores.data.map((s) => <ScoreBadge key={s.id} s={s} />)}
    </div>
  );
}
