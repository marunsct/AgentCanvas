/** Production (S13 FR-EVAL-06): online evaluators on a deployment's traffic, their hourly or daily scores, the latest
 * scored runs and open drift alerts. Everyone can read; builders change things. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { AlertOut, BucketOut, DeploymentOut, EvaluatorOut, OnlineOut, ScoredRunOut } from "../api/generated";
import { can } from "../api/session";
import { QueryStatus, errorText } from "../ui/QueryStatus";
import { fmt, pct } from "./compare";
import { ScoreBadge } from "./ScoreBadge";

const usd = (v: number) => (v ? `$${v.toFixed(v < 0.01 ? 4 : 2)}` : "—");

export function Production({ wfId }: { wfId: string }) {
  const deps = useQuery({ queryKey: ["deployments", wfId], queryFn: () => apiFetch<DeploymentOut[]>(`/workflows/${wfId}/deployments`) });
  const alerts = useQuery({ queryKey: ["alerts", wfId], queryFn: () => apiFetch<AlertOut[]>(`/workflows/${wfId}/alerts`), refetchInterval: 60_000 });
  const [picked, setPicked] = useState("");
  const live = deps.data?.filter((d) => d.active) ?? [];
  const dep = live.find((d) => d.id === picked) ?? live[0];
  return (
    <section className="pad">
      <p className="small muted">
        Online evaluators score a sample of a deployment's runs after the caller has its answer. Filter on the run's status or
        the <code>metadata</code> the caller sent. A drift rule raises an alert when the mean over a window falls below a
        minimum, or too far below its 7-day mean. What the judges cost is shown here, apart from the runs' cost.
      </p>
      {alerts.data && alerts.data.length > 0 && (
        <ul className="alerts" data-testid="eval-alerts">
          {alerts.data.map((a) => (
            <li key={a.id} role="alert" className="error-text">
              {a.message} <span className="muted small">since {new Date(a.created_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
      <QueryStatus q={deps} what="deployments" empty={<p className="muted">Publish the workflow to score its production runs.</p>} />
      {live.length > 1 && (
        <select aria-label="deployment" value={dep?.id ?? ""} onChange={(e) => setPicked(e.target.value)}>
          {live.map((d) => <option key={d.id} value={d.id}>{d.environment} v{d.semver}</option>)}
        </select>
      )}
      {dep && <DeploymentScores wfId={wfId} dep={dep} />}
    </section>
  );
}

function DeploymentScores({ wfId, dep }: { wfId: string; dep: DeploymentOut }) {
  const qc = useQueryClient();
  const key = ["online", dep.id];
  const list = useQuery({ queryKey: key, queryFn: () => apiFetch<OnlineOut[]>(`/deployments/${dep.id}/online-evaluators`) });
  const evaluators = useQuery({ queryKey: ["evaluators", wfId], queryFn: () => apiFetch<EvaluatorOut[]>(`/workflows/${wfId}/evaluators`) });
  const [bucket, setBucket] = useState<"hour" | "day">("hour");
  const summary = useQuery({
    queryKey: ["scores", dep.id, bucket],
    queryFn: () => apiFetch<BucketOut[]>(`/deployments/${dep.id}/scores?bucket=${bucket}&days=${bucket === "hour" ? 1 : 30}`),
    refetchInterval: 60_000,
  });
  const runs = useQuery({ queryKey: ["scored-runs", dep.id], queryFn: () => apiFetch<ScoredRunOut[]>(`/deployments/${dep.id}/scored-runs?limit=25`), refetchInterval: 60_000 });
  const [form, setForm] = useState({ evaluator_id: "", sampling: "10", status: "", metadata: "", alert_below: "", alert_drop: "" });
  const add = useMutation({
    mutationFn: () => {
      const metadata = Object.fromEntries(form.metadata.split(",").map((p) => p.split("=").map((s) => s.trim())).filter((p) => p.length === 2 && p[0]));
      return apiFetch<OnlineOut>(`/deployments/${dep.id}/online-evaluators`, {
        method: "POST",
        json: {
          evaluator_id: form.evaluator_id || evaluators.data?.[0]?.id,
          sampling: Number(form.sampling) / 100,
          filter: { status: form.status ? [form.status] : null, metadata },
          alert_below: form.alert_below.trim() ? Number(form.alert_below) : null,
          alert_drop: form.alert_drop.trim() ? Number(form.alert_drop) : null,
        },
      });
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
  });
  const remove = useMutation({ mutationFn: (id: string) => apiFetch(`/online-evaluators/${id}`, { method: "DELETE" }), onSuccess: () => void qc.invalidateQueries({ queryKey: key }) });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const describe = (o: OnlineOut) => {
    const parts = [o.filter?.status?.join("/"), ...Object.entries(o.filter?.metadata ?? {}).map(([k, v]) => `${k}=${v}`)].filter(Boolean);
    return parts.length ? parts.join(", ") : "every run";
  };
  const rules = (o: OnlineOut) => [
    o.alert_below != null && `below ${o.alert_below}`,
    o.alert_drop != null && `${o.alert_drop} under the 7-day mean`,
  ].filter(Boolean).join(" or ") || "—";

  return (
    <>
      <h3>Online evaluators on {dep.environment} v{dep.semver}</h3>
      <QueryStatus q={list} what="online evaluators" empty={<p className="muted">No online evaluators: production runs are not scored.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="online-evaluators">
          <thead><tr><th>Evaluator</th><th>Sampled</th><th>Runs</th><th>Alert when</th><th /></tr></thead>
          <tbody>
            {list.data.map((o) => (
              <tr key={o.id}>
                <td>{o.evaluator}</td>
                <td>{pct(o.sampling ?? 1)}</td>
                <td className="small">{describe(o)}</td>
                <td className="small">{rules(o)} <span className="muted">({o.window_minutes} min, ≥ {o.min_samples} runs)</span></td>
                <td>{can("builder") && <button type="button" className="link small" onClick={() => remove.mutate(o.id)}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {can("builder") && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }} data-testid="online-form">
          <div className="row gap">
            <select aria-label="online evaluator" value={form.evaluator_id} onChange={set("evaluator_id")}>
              {evaluators.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
            <label className="row small">sample %<input aria-label="sampling percent" value={form.sampling} onChange={set("sampling")} style={{ width: 50 }} /></label>
            <select aria-label="run status filter" value={form.status} onChange={set("status")}>
              <option value="">any status</option>
              {["success", "error", "interrupted", "cancelled"].map((s) => <option key={s}>{s}</option>)}
            </select>
            <input aria-label="metadata filter" placeholder="metadata, e.g. tier=gold" value={form.metadata} onChange={set("metadata")} style={{ width: 170 }} />
            <input aria-label="alert below" placeholder="alert below" value={form.alert_below} onChange={set("alert_below")} style={{ width: 90 }} />
            <input aria-label="alert drop" placeholder="alert drop" value={form.alert_drop} onChange={set("alert_drop")} style={{ width: 90 }} />
            <button type="submit" disabled={add.isPending || !evaluators.data?.length}>Score runs</button>
          </div>
          {add.error && <p role="alert" className="error-text">{errorText(add.error)}</p>}
        </form>
      )}

      <h3>
        Scores{" "}
        <select aria-label="bucket" value={bucket} onChange={(e) => setBucket(e.target.value as "hour" | "day")}>
          <option value="hour">by hour, last day</option>
          <option value="day">by day, last 30 days</option>
        </select>
      </h3>
      <QueryStatus q={summary} what="scores" empty={<p className="muted">No scores yet.</p>} />
      {summary.data && summary.data.length > 0 && (
        <table className="table small" data-testid="score-buckets">
          <thead><tr><th>From</th><th>Evaluator</th><th>Runs</th><th>Mean</th><th>Passed</th><th>Judge cost</th></tr></thead>
          <tbody>
            {summary.data.map((b) => (
              <tr key={`${b.start}-${b.evaluator}`}>
                <td>{new Date(b.start).toLocaleString()}</td>
                <td>{b.evaluator}</td>
                <td>{b.runs}</td>
                <td>{fmt(b.mean)}</td>
                <td>{pct(b.pass_rate)}</td>
                <td>{usd(b.judge_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Latest scored runs</h3>
      <QueryStatus q={runs} what="scored runs" empty={<p className="muted">No scored runs yet.</p>} />
      {runs.data && runs.data.length > 0 && (
        <table className="table small" data-testid="scored-runs">
          <thead><tr><th>Started</th><th>Status</th><th>Scores</th><th /></tr></thead>
          <tbody>
            {runs.data.map((r) => (
              <tr key={r.run_id}>
                <td>{new Date(r.started_at).toLocaleString()}</td>
                <td>{r.status}</td>
                <td>
                  {r.scores.map((s) => <ScoreBadge key={s.id} s={s} />)}
                </td>
                <td><Link className="small" to={`/workflows/${wfId}?thread=${r.thread_id}`}>timeline</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
