/** Annotation queues (S13): rules route flagged production runs to people, who score them per rubric criterion,
 * comment, and can add the run with a corrected answer to a dataset in one step. Reviewers and up review;
 * builders set queues up. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { DatasetOut, ItemDetail, ItemOut, QueueOut } from "../api/generated";
import { can } from "../api/session";
import { QueryStatus, errorText } from "../ui/QueryStatus";
import { pct } from "./compare";
import { ScoreBadge } from "./ScoreBadge";

const REASONS: Record<string, string> = { manual: "added by hand", failed: "failed", thumbs_down: "thumbs-down" };
const reason = (r: string) => REASONS[r] ?? (r.startsWith("score:") ? `low ${r.slice(6)} score` : r);

export function Queues({ wfId }: { wfId: string }) {
  const qc = useQueryClient();
  const key = ["queues", wfId];
  const list = useQuery({ queryKey: key, queryFn: () => apiFetch<QueueOut[]>(`/workflows/${wfId}/annotation-queues`) });
  const [open, setOpen] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [criteria, setCriteria] = useState("helpful:5, correct:2");
  const [rules, setRules] = useState({ failed: true, thumbs_down: true, below: "" });
  const create = useMutation({
    mutationFn: () => {
      const rubric = criteria.split(",").map((c) => c.trim()).filter(Boolean).map((c) => {
        const [n, scale] = c.split(":").map((s) => s.trim());
        return { name: n, scale: Number(scale || 5) };
      });
      const scores_below = Object.fromEntries(rules.below.split(",").map((p) => p.split("<").map((s) => s.trim())).filter((p) => p.length === 2 && p[0]).map(([k, v]) => [k, Number(v)]));
      return apiFetch<QueueOut>(`/workflows/${wfId}/annotation-queues`, { method: "POST", json: { name, rubric, rules: { failed: rules.failed, thumbs_down: rules.thumbs_down, scores_below } } });
    },
    onSuccess: (q) => { setName(""); setOpen(q.id); void qc.invalidateQueries({ queryKey: key }); },
  });
  return (
    <section className="pad">
      <p className="small muted">
        Queues collect production runs for people to review: failed runs, runs an end user rated down
        (<code>POST /v1/deployments/&lt;id&gt;/runs/&lt;run&gt;/feedback</code>), runs an online evaluator scored low, and runs added by
        hand. Reviewers score each run on the rubric and can add it, with the right answer, to a dataset.
      </p>
      <QueryStatus q={list} what="annotation queues" empty={<p className="muted">No annotation queues yet.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="queues">
          <thead><tr><th>Queue</th><th>Fed by</th><th>To review</th><th>Done</th><th /></tr></thead>
          <tbody>
            {list.data.map((q) => (
              <tr key={q.id} className={open === q.id ? "selected" : ""}>
                <td>{q.name}</td>
                <td className="small">{[q.rules?.failed && "failed runs", q.rules?.thumbs_down && "thumbs-down", ...Object.entries(q.rules?.scores_below ?? {}).map(([k, v]) => `${k} < ${v}`)].filter(Boolean).join(", ") || "hand only"}</td>
                <td>{q.progress.pending}</td>
                <td>{q.progress.done} of {q.progress.total}</td>
                <td><button type="button" className="link small" onClick={() => setOpen(open === q.id ? null : q.id)}>{open === q.id ? "Close" : "Review"}</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {can("builder") && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); create.mutate(); }} data-testid="queue-form">
          <div className="row gap">
            <input aria-label="queue name" placeholder="queue name" value={name} onChange={(e) => setName(e.target.value)} />
            <input aria-label="rubric" title="criterion:points, comma separated (2 points is yes/no)" value={criteria} onChange={(e) => setCriteria(e.target.value)} style={{ width: 200 }} />
            <label className="row small"><input type="checkbox" checked={rules.failed} onChange={(e) => setRules((r) => ({ ...r, failed: e.target.checked }))} />failed runs</label>
            <label className="row small"><input type="checkbox" checked={rules.thumbs_down} onChange={(e) => setRules((r) => ({ ...r, thumbs_down: e.target.checked }))} />thumbs-down</label>
            <input aria-label="low score rule" placeholder="e.g. polite < 0.7" value={rules.below} onChange={(e) => setRules((r) => ({ ...r, below: e.target.value }))} style={{ width: 140 }} />
            <button type="submit" disabled={create.isPending || !name.trim()}>Create queue</button>
          </div>
          {create.error && <p role="alert" className="error-text">{errorText(create.error)}</p>}
        </form>
      )}
      {open && <QueueReview wfId={wfId} queueId={open} />}
    </section>
  );
}

function QueueReview({ wfId, queueId }: { wfId: string; queueId: string }) {
  const queue = useQuery({ queryKey: ["queue", queueId], queryFn: () => apiFetch<QueueOut>(`/annotation-queues/${queueId}`) });
  const [status, setStatus] = useState<"pending" | "done" | "all">("pending");
  const items = useQuery({ queryKey: ["queue-items", queueId, status], queryFn: () => apiFetch<ItemOut[]>(`/annotation-queues/${queueId}/items?status=${status}`) });
  const [current, setCurrent] = useState<string | null>(null);
  const firstOpen = items.data?.find((i) => !i.reviewed_by_me)?.id ?? null;
  const active = current ?? firstOpen;
  const q = queue.data;
  const qc = useQueryClient();
  const [runIds, setRunIds] = useState("");
  const add = useMutation({
    mutationFn: () => apiFetch<ItemOut[]>(`/annotation-queues/${queueId}/items`, { method: "POST", json: { run_ids: runIds.split(/[\s,]+/).filter(Boolean) } }),
    onSuccess: () => { setRunIds(""); void qc.invalidateQueries({ queryKey: ["queue-items", queueId] }); void qc.invalidateQueries({ queryKey: ["queue", queueId] }); },
  });
  return (
    <div className="queue-review" data-testid="queue-review">
      {q && (
        <p className="small">
          <strong>{q.name}</strong>: {q.progress.done} of {q.progress.total} done, {q.reviewers_per_item} reviewer(s) per run.
          {" "}Agreement: {Object.entries(q.agreement ?? {}).map(([k, v]) => `${k} ${v == null ? "—" : pct(v)}`).join(", ") || "—"}
          {q.instructions && <><br /><span className="muted">{q.instructions}</span></>}
        </p>
      )}
      <div className="row gap small">
        <select aria-label="item status" value={status} onChange={(e) => { setStatus(e.target.value as typeof status); setCurrent(null); }}>
          <option value="pending">to review</option><option value="done">done</option><option value="all">all</option>
        </select>
        {items.data?.map((i) => (
          <button type="button" key={i.id} className={`link small${i.id === active ? " active" : ""}`} onClick={() => setCurrent(i.id)} title={reason(i.reason)}>
            {i.run_id.slice(-6)}{i.reviewed_by_me ? " ✓" : ""}
          </button>
        ))}
      </div>
      {can("reviewer") && (
        <form className="row gap small" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <input aria-label="run ids" placeholder="add runs by id" value={runIds} onChange={(e) => setRunIds(e.target.value)} style={{ width: 260 }} />
          <button type="submit" disabled={add.isPending || !runIds.trim()}>Add</button>
          {add.error && <span role="alert" className="error-text">{errorText(add.error)}</span>}
        </form>
      )}
      <QueryStatus q={items} what="items" empty={<p className="muted">Nothing to review.</p>} />
      {active && q && <ItemReview wfId={wfId} queue={q} itemId={active} onDone={() => setCurrent(null)} />}
    </div>
  );
}

function ItemReview({ wfId, queue, itemId, onDone }: { wfId: string; queue: QueueOut; itemId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const item = useQuery({ queryKey: ["queue-item", itemId], queryFn: () => apiFetch<ItemDetail>(`/annotation-items/${itemId}`) });
  const datasets = useQuery({ queryKey: ["datasets", wfId], queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${wfId}/datasets`) });
  const [scores, setScores] = useState<Record<string, number>>({});
  const [comment, setComment] = useState("");
  const [dataset, setDataset] = useState("");
  const [reference, setReference] = useState("");
  useEffect(() => {
    setScores({}); setComment(""); setDataset("");
    setReference(item.data?.output ? JSON.stringify(item.data.output, null, 2) : "");
  }, [itemId, item.data?.output]);
  const submit = useMutation({
    mutationFn: () => {
      let add: { dataset_id: string; reference: Record<string, unknown> } | undefined;
      if (dataset) {
        try { add = { dataset_id: dataset, reference: JSON.parse(reference || "{}") as Record<string, unknown> }; } catch { throw new Error("the corrected answer must be JSON"); }
      }
      return apiFetch<ItemDetail>(`/annotation-items/${itemId}/annotations`, { method: "POST", json: { scores, comment, add_to_dataset: add } });
    },
    onSuccess: () => {
      for (const k of [["queue", queue.id], ["queue-items", queue.id], ["queue-item", itemId], ["queues", wfId], ["datasets", wfId]]) void qc.invalidateQueries({ queryKey: k });
      onDone();
    },
  });
  const d = item.data;
  if (!d) return <QueryStatus q={item} what="item" />;
  const complete = queue.rubric.every((c) => scores[c.name] != null);
  return (
    <div className="item-review" data-testid="item-review">
      <p className="small">
        Run {d.run_id} · {reason(d.reason)} · {d.run_status}
        {" "}<Link to={`/workflows/${wfId}?thread=${d.thread_id}`}>timeline</Link>
      </p>
      <div className="row gap">
        <div><h4>Input</h4><pre className="json small">{JSON.stringify(d.input ?? {}, null, 2)}</pre></div>
        <div><h4>Output</h4><pre className="json small">{d.error ?? JSON.stringify(d.output ?? {}, null, 2)}</pre></div>
      </div>
      {d.scores.length > 0 && <div className="row gap small">{d.scores.map((s) => <ScoreBadge key={s.id} s={s} />)}</div>}
      {d.annotations.length > 0 && (
        <ul className="small">
          {d.annotations.map((a) => <li key={a.reviewer}>{a.reviewer}: {Object.entries(a.scores).map(([k, v]) => `${k} ${v}`).join(", ")}{a.comment && ` · ${a.comment}`}</li>)}
        </ul>
      )}
      {can("reviewer") && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }} data-testid="annotate-form">
          {queue.rubric.map((c) => (
            <div key={c.name} className="row gap small" role="radiogroup" aria-label={c.name}>
              <span title={c.description ?? ""} style={{ width: 120 }}>{c.name}</span>
              {Array.from({ length: c.scale ?? 5 }, (_, i) => i + 1).map((p) => (
                <label key={p} className="row"><input type="radio" name={c.name} checked={scores[c.name] === p} onChange={() => setScores((s) => ({ ...s, [c.name]: p }))} />{c.scale === 2 ? (p === 2 ? "yes" : "no") : p}</label>
              ))}
            </div>
          ))}
          <textarea aria-label="comment" placeholder="comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
          <div className="row gap small">
            <select aria-label="add to dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
              <option value="">don't add to a dataset</option>
              {datasets.data?.map((x) => <option key={x.id} value={x.id}>add to {x.name}</option>)}
            </select>
          </div>
          {dataset && <textarea aria-label="corrected answer" className="json small" value={reference} onChange={(e) => setReference(e.target.value)} rows={4} />}
          <button type="submit" disabled={submit.isPending || !complete}>Submit review</button>
          {submit.error && <p role="alert" className="error-text">{errorText(submit.error)}</p>}
        </form>
      )}
    </div>
  );
}
