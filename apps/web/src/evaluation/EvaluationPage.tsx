/** Evaluation Studio (S12): datasets, evaluators and experiments for one workflow, with a results grid and a
 * comparison of two experiments. Everyone can read; builders change things. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { apiDownload, apiFetch } from "../api/client";
import type {
  DatasetDetail, DatasetOut, GateOut, DatasetVersionOut, EvaluatorOut, ExperimentOut, ExperimentResultOut, Score, VersionOut,
} from "../api/generated";
import { useFrameworks } from "../api/queries";
import { can } from "../api/session";
import { QueryStatus, errorText } from "../ui/QueryStatus";
import { compare, fmt, frameworksToCompare, grid, passed, pct } from "./compare";
import { Production } from "./Production";
import { Queues } from "./Queues";
import { ScoreBadge } from "./ScoreBadge";
import { confirmDialog, toast } from "../ui/dialog";
import { EvaluatorForm, useEvaluatorKinds } from "./EvaluatorForm";
import { ExampleForm } from "./ExampleForm";
import { fromRows, OverrideEditor } from "./OverrideEditor";
import { PageHeader } from "../ui/PageHeader";
import { NEW_RED_TEAM, RedTeamFields, redTeamBody, type RedTeamDraft } from "./RedTeam";

type Tab = "datasets" | "evaluators" | "experiments" | "gates" | "production" | "queues";

function parseJson(text: string, what: string): Record<string, unknown> {
  if (!text.trim()) return {};
  try {
    const v = JSON.parse(text) as unknown;
    if (v && typeof v === "object" && !Array.isArray(v)) return v as Record<string, unknown>;
  } catch { /* reported below */ }
  throw new Error(`${what} must be a JSON object`);
}

const short = (v: unknown) => {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 80 ? `${s.slice(0, 77)}…` : s;
};

export function EvaluationPage() {
  const { id = "" } = useParams();
  const [tab, setTab] = useState<Tab>("datasets");
  const wf = useQuery({
    queryKey: ["workflow", id],
    queryFn: () => apiFetch<{ name: string; ir: {
      framework?: string; state?: { channels?: { name: string; io?: string | null }[] };
      nodes?: Element[]; resources?: Element[];
    } }>(`/workflows/${id}`),
  });
  return (
    <div className="page evaluation">
      <PageHeader title="Evaluation" crumbs={[{ to: "/", label: "Workflows" }, { to: `/workflows/${id}`, label: wf.data?.name ?? "Workflow", testId: "back-to-workflow" }]}
        description="Datasets, evaluators and experiments for this workflow; release gates that keep a regression from being published." />
      <nav className="tabs" role="tablist">
        {(["datasets", "evaluators", "experiments", "gates", "production", "queues"] as Tab[]).map((t) => (
          <button type="button" role="tab" key={t} aria-selected={tab === t} className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}>{{ datasets: "Datasets", evaluators: "Evaluators", experiments: "Experiments", gates: "Release gates", production: "Production", queues: "Annotation queues" }[t]}</button>
        ))}
      </nav>
      {tab === "datasets" && <Datasets wfId={id} channels={wf.data?.ir.state?.channels ?? []} />}
      {tab === "evaluators" && <Evaluators wfId={id} />}
      {tab === "experiments" && <Experiments wfId={id} wfFramework={wf.data?.ir.framework ?? "langchain"}
        elements={[...(wf.data?.ir.nodes ?? []), ...(wf.data?.ir.resources ?? [])]} />}
      {tab === "gates" && <Gates wfId={id} />}
      {tab === "production" && <Production wfId={id} />}
      {tab === "queues" && <Queues wfId={id} />}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ datasets ----
function Datasets({ wfId, channels }: { wfId: string; channels: { name: string; io?: string | null }[] }) {
  const qc = useQueryClient();
  const key = ["datasets", wfId];
  const list = useQuery({ queryKey: key, queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${wfId}/datasets`) });
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => apiFetch<DatasetDetail>(`/workflows/${wfId}/datasets`, { method: "POST", json: { name } }),
    onSuccess: (d) => { setName(""); setOpen(d.id); void qc.invalidateQueries({ queryKey: key }); },
  });
  const builder = can("builder");
  return (
    <section className="pad">
      {builder && (
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <input required placeholder="New dataset name" aria-label="dataset name" value={name} onChange={(e) => setName(e.target.value)} />
          <button type="submit" disabled={create.isPending}>Create dataset</button>
          {create.error && <span role="alert" className="error-text">{errorText(create.error)}</span>}
        </form>
      )}
      <QueryStatus q={list} what="datasets" empty={<p className="muted">No datasets yet.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="datasets">
          <thead><tr><th>Name</th><th>Version</th><th>Examples</th><th>Splits</th><th /></tr></thead>
          <tbody>
            {list.data.map((d) => (
              <tr key={d.id} className={open === d.id ? "selected" : ""}>
                <td><button type="button" className="link" onClick={() => setOpen(d.id)}>{d.name}</button></td>
                <td>v{d.version}</td><td>{d.examples}</td>
                <td className="small">{Object.entries(d.splits).map(([s, n]) => `${s || "(none)"}: ${n}`).join(", ")}</td>
                <td><button type="button" className="link small" onClick={() => void apiDownload(`/datasets/${d.id}/export`, `${d.name}.jsonl`)}>Export</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <DatasetView dsId={open} channels={channels} onGone={() => { setOpen(null); void qc.invalidateQueries({ queryKey: key }); }} />}
    </section>
  );
}

function DatasetView({ dsId, channels, onGone }: { dsId: string; channels: { name: string; io?: string | null }[]; onGone: () => void }) {
  const qc = useQueryClient();
  const [version, setVersion] = useState<number | null>(null);
  const key = ["dataset", dsId];
  const ds = useQuery({
    queryKey: [...key, version],
    queryFn: () => apiFetch<DatasetDetail>(`/datasets/${dsId}${version ? `?version=${version}` : ""}`),
  });
  const versions = useQuery({ queryKey: [...key, "versions", ds.data?.version], queryFn: () => apiFetch<DatasetVersionOut[]>(`/datasets/${dsId}/versions`) });
  const changed = () => { setVersion(null); void qc.invalidateQueries({ queryKey: key }); void qc.invalidateQueries({ queryKey: ["datasets"] }); };
  const add = useMutation({
    mutationFn: (example: { input: Record<string, unknown>; reference: Record<string, unknown>; split: string }) =>
      apiFetch(`/datasets/${dsId}/examples`, { method: "POST", json: { examples: [example] } }),
    onSuccess: changed,
  });
  const fromTests = useMutation({ mutationFn: () => apiFetch(`/datasets/${dsId}/examples/from-test-cases`, { method: "POST", json: {} }), onSuccess: changed });
  const upload = useMutation({
    mutationFn: async (file: File) => apiFetch(`/datasets/${dsId}/import`, {
      method: "POST", json: { format: file.name.endsWith(".csv") ? "csv" : "jsonl", content: await file.text(), split: null },
    }),
    onSuccess: changed,
  });
  const remove = useMutation({ mutationFn: (exId: string) => apiFetch(`/datasets/${dsId}/examples/${exId}`, { method: "DELETE" }), onSuccess: changed });
  const drop = useMutation({ mutationFn: () => apiFetch(`/datasets/${dsId}`, { method: "DELETE" }), onSuccess: onGone });
  const push = useMutation({ mutationFn: () => apiFetch(`/datasets/${dsId}/langsmith/push`, { method: "POST", json: { version } }), onSuccess: changed });
  const pull = useMutation({ mutationFn: () => apiFetch(`/datasets/${dsId}/langsmith/pull`, { method: "POST" }), onSuccess: changed });
  const error = add.error ?? fromTests.error ?? upload.error ?? remove.error ?? drop.error ?? push.error ?? pull.error;
  if (!ds.data) return <QueryStatus q={ds} what="the dataset" />;
  const d = ds.data;
  const latest = version === null;
  const builder = can("builder") && latest;
  return (
    <div className="dataset-view" data-testid="dataset-view">
      <div className="row gap">
        <h3>{d.name}</h3>
        <select aria-label="dataset version" value={version ?? ""} onChange={(e) => setVersion(e.target.value ? Number(e.target.value) : null)}>
          <option value="">latest (v{versions.data?.[0]?.number ?? d.version})</option>
          {versions.data?.slice(1).map((v) => <option key={v.id} value={v.number}>v{v.number} · {v.examples} examples</option>)}
        </select>
        {!latest && <span className="muted small">Older versions are read-only.</span>}
        <span className="spacer" />
        {can("builder") && (
          <button type="button" className="link small" onClick={async () => { if (await confirmDialog({ title: `Delete the dataset ${d.name}?`, body: "Every version and its examples are deleted.", confirm: "Delete", danger: true })) drop.mutate(); }}>Delete dataset</button>
        )}
      </div>
      <p className="small" data-testid="langsmith-link">
        {d.langsmith
          ? <>LangSmith: {d.langsmith.url ? <a href={d.langsmith.url} target="_blank" rel="noreferrer">{d.langsmith.name} ↗</a> : d.langsmith.name}
              {d.langsmith.pushed_version != null && ` · v${d.langsmith.pushed_version} pushed`}
              {d.langsmith.pulled_version != null && ` · v${d.langsmith.pulled_version} pulled`}
              {" "}<span className="muted">{new Date(d.langsmith.synced_at).toLocaleString()}</span></>
          : <span className="muted">Not synced with LangSmith.</span>}
        {can("builder") && (
          <>
            {" "}<button type="button" className="link small" disabled={push.isPending} onClick={() => push.mutate()}>Push {version ? `v${version}` : "latest"} to LangSmith</button>
            {d.langsmith && latest && <> · <button type="button" className="link small" disabled={pull.isPending}
              onClick={async () => { if (await confirmDialog({ title: "Pull from LangSmith?", body: "The examples are replaced with LangSmith's, as a new version.", confirm: "Pull" })) pull.mutate(); }}>Pull from LangSmith</button></>}
          </>
        )}
      </p>
      {error && <p role="alert" className="error-text">{errorText(error)}</p>}
      <table className="table" data-testid="examples">
        <thead><tr><th>#</th><th>Input</th><th>Reference</th><th>Split</th><th /></tr></thead>
        <tbody>
          {d.items.map((ex, i) => (
            <tr key={ex.id}>
              <td>{i + 1}</td><td><code>{short(ex.input)}</code></td><td><code>{short(ex.reference ?? {})}</code></td><td>{ex.split}</td>
              <td>{builder && <button type="button" className="link small" onClick={async () => {
                if (await confirmDialog({ title: `Remove example ${i + 1}?`, body: "The dataset gets a new version without it; older versions keep it.", confirm: "Remove", danger: true })) remove.mutate(ex.id!);
              }}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {builder && (
        <>
          <ExampleForm channels={channels} busy={add.isPending} onAdd={(ex) => add.mutate(ex)} />
          <div className="row gap small">
            <button type="button" className="link small" onClick={() => fromTests.mutate()}>Add the saved test cases</button>
            <label className="small">Import CSV or JSONL <input type="file" accept=".csv,.jsonl,.json" aria-label="import file"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} /></label>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------- evaluators ----
function Evaluators({ wfId }: { wfId: string }) {
  const qc = useQueryClient();
  const key = ["evaluators", wfId];
  const list = useQuery({ queryKey: key, queryFn: () => apiFetch<EvaluatorOut[]>(`/workflows/${wfId}/evaluators`) });
  const kinds = useEvaluatorKinds();
  const label = (k: string) => kinds.data?.find((x) => x.kind === k)?.label ?? k;
  const [form, setForm] = useState<"new" | EvaluatorOut | null>(null);
  const remove = useMutation({
    mutationFn: (eid: string) => apiFetch(`/evaluators/${eid}`, { method: "DELETE" }),
    onSuccess: () => { toast("Deleted the evaluator"); void qc.invalidateQueries({ queryKey: key }); },
  });
  const [trying, setTrying] = useState<string | null>(null);
  const builder = can("builder");
  return (
    <section className="pad">
      {builder && !form && <button type="button" className="primary" onClick={() => setForm("new")}>+ New evaluator</button>}
      {builder && form && (
        <EvaluatorForm key={form === "new" ? "new" : form.id} wfId={wfId} editing={form === "new" ? undefined : form}
          onDone={() => setForm(null)} />
      )}
      <QueryStatus q={list} what="evaluators" empty={<p className="muted">No evaluators yet.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="evaluators">
          <thead><tr><th>Name</th><th>Kind</th><th>Settings</th><th>Passes at</th><th /></tr></thead>
          <tbody>
            {list.data.map((e) => (
              <tr key={e.id}>
                <td>{e.name}</td><td>{label(e.kind)}</td><td><code>{short(e.config ?? {})}</code></td><td>{e.effective_threshold}</td>
                <td className="row gap">
                  <button type="button" className="link small" onClick={() => setTrying(trying === e.id ? null : e.id)}>Try</button>
                  {builder && <button type="button" className="link small" onClick={() => setForm(e)}>Edit</button>}
                  {builder && <button type="button" className="link small" onClick={async () => {
                    if (await confirmDialog({ title: `Delete the evaluator ${e.name}?`, body: "Experiments that used it keep their scores.", confirm: "Delete", danger: true })) remove.mutate(e.id);
                  }}>Delete</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {trying && <TryEvaluator key={trying} evaluatorId={trying} />}
    </section>
  );
}

function TryEvaluator({ evaluatorId }: { evaluatorId: string }) {
  const [sample, setSample] = useState('{\n  "input": {},\n  "output": {"out": ""},\n  "reference": {"out": ""}\n}');
  const run = useMutation({
    mutationFn: () => apiFetch<Score>(`/evaluators/${evaluatorId}/try`, { method: "POST", json: parseJson(sample, "the sample") }),
  });
  return (
    <div className="policy-form" data-testid="try-evaluator">
      <textarea aria-label="sample" rows={6} value={sample} onChange={(e) => setSample(e.target.value)} />
      <div className="row gap">
        <button type="button" onClick={() => run.mutate()} disabled={run.isPending}>Score this sample</button>
        {run.data && <ScoreBadge s={run.data} />}
        {run.data?.comment && <span className="small muted">{run.data.comment}</span>}
        {run.error && <span role="alert" className="error-text">{errorText(run.error)}</span>}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------- experiments ----
interface TargetDraft { version: string; changes: { element: string; field: string; value: string }[]; label: string; framework: string }
const NEW_TARGET: TargetDraft = { version: "", changes: [], label: "", framework: "" };

type Element = { id: string; name: string; label?: string | null; type: string; config?: Record<string, unknown> };

function Experiments({ wfId, wfFramework, elements }: { wfId: string; wfFramework: string; elements: Element[] }) {
  const qc = useQueryClient();
  const key = ["experiments", wfId];
  const list = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<ExperimentOut[]>(`/workflows/${wfId}/experiments`),
    refetchInterval: (q) => (q.state.data?.some((e) => e.status === "running") ? 1000 : false),
  });
  const datasets = useQuery({ queryKey: ["datasets", wfId], queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${wfId}/datasets`) });
  const versions = useQuery({ queryKey: ["versions", wfId], queryFn: () => apiFetch<VersionOut[]>(`/workflows/${wfId}/versions`) });
  const [dataset, setDataset] = useState("");
  const [split, setSplit] = useState("");
  const [targets, setTargets] = useState<TargetDraft[]>([NEW_TARGET]);
  const { data: frameworks = [] } = useFrameworks();
  const [open, setOpen] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string>("");
  const [sim, setSim] = useState({ on: false, model: "", persona: "", goal: "", max_turns: "5" });
  const [rt, setRt] = useState<RedTeamDraft>(NEW_RED_TEAM);
  const start = useMutation({
    mutationFn: () => {
      const given = targets.map((t) => ({
        version: t.version || null, label: t.label || null, framework: t.framework || null,
        overrides: fromRows(t.changes),
      }));
      if (rt.on) return apiFetch<ExperimentOut>(`/workflows/${wfId}/red-team`, { method: "POST", json: redTeamBody(rt, given) });
      return apiFetch<ExperimentOut>(`/workflows/${wfId}/experiments`, {
        method: "POST",
        json: {
          dataset_id: dataset || datasets.data?.[0]?.id, split: split || null, targets: given,
          simulation: sim.on ? { model: sim.model.trim() || null, persona: sim.persona, goal: sim.goal, max_turns: Number(sim.max_turns) || 5 } : null,
        },
      });
    },
    onSuccess: (e) => { setOpen(e.id); void qc.invalidateQueries({ queryKey: key }); },
  });
  const cancel = useMutation({ mutationFn: (eid: string) => apiFetch(`/experiments/${eid}/cancel`, { method: "POST" }), onSuccess: () => void qc.invalidateQueries({ queryKey: key }) });
  const setTarget = (i: number, patch: Partial<TargetDraft>) => setTargets((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const builder = can("builder");
  const selected = list.data?.find((e) => e.id === open);
  const before = selected && baseline !== selected.id ? list.data?.find((e) => e.id === baseline) : undefined;
  return (
    <section className="pad">
      {builder && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); start.mutate(); }} data-testid="start-experiment">
          <div className="row gap">
            {!rt.on && (
              <>
                <select aria-label="experiment dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                  {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name} (v{d.version}, {d.examples} examples)</option>)}
                </select>
                <input aria-label="experiment split" placeholder="split (all)" value={split} onChange={(e) => setSplit(e.target.value)} style={{ width: 110 }} />
              </>
            )}
            <button type="submit" className="primary" disabled={start.isPending || (rt.on ? rt.categories.length === 0 : !datasets.data?.length)}>
              {rt.on ? "▶ Run red team" : "▶ Run experiment"}
            </button>
          </div>
          {targets.map((t, i) => (
            <div className="row gap" key={i}>
              <select aria-label={`target ${i + 1} version`} value={t.version} onChange={(e) => setTarget(i, { version: e.target.value })}>
                <option value="">draft</option>
                {versions.data?.map((v) => <option key={v.id} value={v.semver}>v{v.semver}</option>)}
              </select>
              {frameworks.length > 1 && (
                <select aria-label={`target ${i + 1} framework`} value={t.framework} onChange={(e) => setTarget(i, { framework: e.target.value })}
                  title="convert and compare: run this target on another framework">
                  <option value="">its own framework</option>
                  {frameworks.map((f) => <option key={f.id} value={f.id}>on {f.label}</option>)}
                </select>
              )}
              <input aria-label={`target ${i + 1} label`} placeholder="label" value={t.label} onChange={(e) => setTarget(i, { label: e.target.value })} style={{ width: 110 }} />
              {targets.length > 1 && <button type="button" className="link small" onClick={() => setTargets((ts) => ts.filter((_, j) => j !== i))}>Remove</button>}
              <OverrideEditor label={`target ${i + 1}`} elements={elements} rows={t.changes} onChange={(changes) => setTarget(i, { changes })} />
            </div>
          ))}
          <div className="row gap">
            <button type="button" className="link small" onClick={() => setTargets((ts) => [...ts, NEW_TARGET])}>+ Add a version or variant</button>
            {frameworks.length > 1 && (
              <button type="button" className="link small" data-testid="compare-frameworks"
                onClick={() => setTargets((ts) => [...ts, ...frameworksToCompare(frameworks, wfFramework, ts, () => ({ version: "", changes: [], label: "" }))])}>
                + Compare on the other frameworks
              </button>
            )}
          </div>
          <label className="row small" title="chat workflows: a model plays the user and holds a conversation per example">
            <input type="checkbox" checked={sim.on} onChange={(e) => setSim((x) => ({ ...x, on: e.target.checked }))} />simulated user
          </label>
          {sim.on && (
            <div className="row gap small" data-testid="simulation">
              <input aria-label="simulated user model" placeholder="the project's (Settings › Models)" value={sim.model} onChange={(e) => setSim((x) => ({ ...x, model: e.target.value }))} style={{ width: 200 }} />
              <input aria-label="persona" placeholder="persona: who the user is, how they write" value={sim.persona} onChange={(e) => setSim((x) => ({ ...x, persona: e.target.value }))} style={{ width: 260 }} />
              <input aria-label="goal" placeholder="goal (an example's metadata.goal wins)" value={sim.goal} onChange={(e) => setSim((x) => ({ ...x, goal: e.target.value }))} style={{ width: 240 }} />
              <label className="row">max turns<input aria-label="max turns" value={sim.max_turns} onChange={(e) => setSim((x) => ({ ...x, max_turns: e.target.value }))} style={{ width: 40 }} /></label>
            </div>
          )}
          <label className="row small" title="make adversarial inputs (prompt injection, jailbreaks, PII extraction, off-topic), run them and score whether the workflow held">
            <input type="checkbox" checked={rt.on} onChange={(e) => setRt((x) => ({ ...x, on: e.target.checked }))} />red team
          </label>
          {rt.on && <RedTeamFields value={rt} onChange={setRt} />}
          {start.error && <p role="alert" className="error-text">{errorText(start.error)}</p>}
        </form>
      )}
      <QueryStatus q={list} what="experiments" empty={<p className="muted">No experiments yet.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="experiments">
          <thead><tr><th>Experiment</th><th>Status</th><th>Targets: pass rate</th><th>Progress</th><th /></tr></thead>
          <tbody>
            {list.data.map((e) => {
              const done = e.summary.reduce((n, s) => n + s.completed, 0);
              const all = e.summary.reduce((n, s) => n + s.examples, 0);
              return (
                <tr key={e.id} className={open === e.id ? "selected" : ""}>
                  <td><button type="button" className="link" onClick={() => setOpen(e.id)}>{e.name}</button></td>
                  <td><span className={`badge status-${e.status === "done" ? "ok" : e.status === "running" ? "running" : "error"}`}>{e.status}</span></td>
                  <td className="small">{e.summary.map((s) => `${s.label}: ${pct(s.pass_rate)}`).join(" · ")}</td>
                  <td>{done}/{all}</td>
                  <td>{builder && e.status === "running" && <button type="button" className="link small" onClick={() => cancel.mutate(e.id)}>Cancel</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {selected && (
        <>
          <Results key={selected.id} wfId={wfId} exp={selected} />
          <div className="row gap small">
            Compare with
            <select aria-label="baseline experiment" value={before?.id ?? ""} onChange={(e) => setBaseline(e.target.value)}>
              <option value="">—</option>
              {list.data?.filter((e) => e.id !== selected.id).map((e) => <option key={e.id} value={e.id}>{e.name} ({e.id.slice(-6)})</option>)}
            </select>
          </div>
          {before && <Comparison before={before} after={selected} />}
        </>
      )}
    </section>
  );
}

const RESULTS_PAGE = 1000; // the API's largest page; an experiment has up to 5000 results

async function allResults(expId: string): Promise<ExperimentResultOut[]> {
  const out: ExperimentResultOut[] = [];
  for (;;) {
    const page = await apiFetch<ExperimentResultOut[]>(`/experiments/${expId}/results?limit=${RESULTS_PAGE}&offset=${out.length}`);
    out.push(...page);
    if (page.length < RESULTS_PAGE) return out;
  }
}

function useResults(exp: ExperimentOut) {
  return useQuery({
    queryKey: ["experiment-results", exp.id, exp.status, exp.summary.map((s) => s.completed).join()],
    queryFn: () => allResults(exp.id),
  });
}

function Results({ wfId, exp }: { wfId: string; exp: ExperimentOut }) {
  const results = useResults(exp);
  const [diffOf, setDiffOf] = useState<string | null>(null);
  if (!results.data) return <QueryStatus q={results} what="results" />;
  const rows = grid(results.data, exp.targets.length);
  const pairwise = new Set(exp.pairwise ?? []);
  const diffRow = diffOf ? rows.find((r) => r.exampleId === diffOf) : undefined;
  return (
    <div className="results" data-testid="results">
      <h3>{exp.name}</h3>
      {exp.error && <p className="error-text">{exp.error}</p>}
      <table className="table small">
        <thead>
          <tr><th />{exp.summary.map((s) => <th key={s.target}>{s.label}</th>)}</tr>
          <tr className="muted">
            <td>pass rate · p50/p95 · cost</td>
            {exp.summary.map((s) => (
              <td key={s.target}>
                <b>{pct(s.pass_rate)}</b> · {s.latency_p50_ms ?? "—"}/{s.latency_p95_ms ?? "—"} ms · ${s.cost_usd.toFixed(4)}
                {(s.judge_cost_usd ?? 0) > 0 && <span title="what the AI judges cost, apart from the runs"> + judges ${(s.judge_cost_usd ?? 0).toFixed(4)}</span>}
                {Boolean(s.judge_unpriced?.length) && <span className="warn-text" title="no catalog price: these judge calls are not in the cost"> (unpriced judge: {s.judge_unpriced!.join(", ")})</span>}
                <div>{s.evaluators.map((e) => pairwise.has(e.evaluator)
                  ? <span key={e.evaluator} className="badge" title={`win rate vs ${exp.summary[0]?.label} (ties count half), 95% interval`} data-testid="win-rate">
                      {e.evaluator} {s.target === 0 ? "baseline" : `wins ${pct(e.mean)}`}{e.ci && ` (${pct(e.ci[0])}–${pct(e.ci[1])})`}
                    </span>
                  : <span key={e.evaluator} className="badge" title={`pass rate ${pct(e.pass_rate)}`}>{e.evaluator} {fmt(e.mean)}</span>)}</div>
              </td>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.exampleId}>
              <td><button type="button" className="link small" onClick={() => setDiffOf(diffOf === row.exampleId ? null : row.exampleId)}>#{row.position + 1}</button></td>
              {row.cells.map((r, t) => (
                <td key={t} className={passed(r) === false ? "failed" : ""}>
                  {!r || r.status !== "done" ? <span className="muted">{r?.status ?? "—"}</span> : (
                    <>
                      <code>{short(r.output ?? r.error ?? "")}</code>
                      <div className="row gap">
                        {r.scores.map((s) => <ScoreBadge key={s.evaluator} s={s} />)}
                        {r.run_status !== "success" && <span className="badge err">{r.run_status}</span>}
                        {r.thread_id && <Link className="small" to={`/workflows/${wfId}?thread=${r.thread_id}`}>timeline</Link>}
                      </div>
                    </>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {diffRow && <OutputDiff row={diffRow} labels={exp.summary.map((s) => s.label)} />}
    </div>
  );
}

/** The outputs of one example side by side, channel by channel; differing channels are marked. */
function OutputDiff({ row, labels }: { row: ReturnType<typeof grid>[number]; labels: string[] }) {
  const channels = [...new Set(row.cells.flatMap((c) => Object.keys(c?.output ?? {})))];
  return (
    <table className="table small" data-testid="output-diff">
      <thead><tr><th>Channel</th>{labels.map((l) => <th key={l}>{l}</th>)}</tr></thead>
      <tbody>
        {channels.map((ch) => {
          const values = row.cells.map((c) => JSON.stringify(c?.output?.[ch] ?? null));
          const differs = new Set(values).size > 1;
          return (
            <tr key={ch} className={differs ? "changed" : ""}>
              <td>{ch}{differs && " ≠"}</td>
              {row.cells.map((c, i) => {
                const v = c?.output?.[ch];
                if (ch === "transcript" && Array.isArray(v)) { // a simulated-user conversation (S13)
                  return (
                    <td key={i} data-testid="transcript">
                      {(v as { role: string; content: string }[]).map((t, j) => (
                        <p key={j} className={t.role === "user" ? "muted" : ""}><b>{t.role === "user" ? "User" : "Assistant"}:</b> {t.content}</p>
                      ))}
                    </td>
                  );
                }
                return <td key={i}><code>{values[i]}</code></td>;
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Comparison({ before, after }: { before: ExperimentOut; after: ExperimentOut }) {
  const a = useResults(before);
  const b = useResults(after);
  if (!a.data || !b.data) return <QueryStatus q={a.data ? b : a} what="results" />;
  const c = compare(before, a.data, after, b.data);
  const sign = (d: number | null) => (d == null ? "—" : `${d > 0 ? "+" : ""}${d.toFixed(2)}`);
  return (
    <div data-testid="comparison">
      <table className="table small">
        <thead><tr><th>Evaluator</th><th>{before.name}</th><th>{after.name}</th><th>Δ</th></tr></thead>
        <tbody>
          {c.evaluators.map((e) => (
            <tr key={e.evaluator}><td>{e.evaluator}</td><td>{fmt(e.before)}</td><td>{fmt(e.after)}</td>
              <td className={e.delta && e.delta < 0 ? "error-text" : ""}>{sign(e.delta)}</td></tr>
          ))}
          <tr><td><b>pass rate</b></td><td>{pct(c.passRate.before)}</td><td>{pct(c.passRate.after)}</td><td>{sign(c.passRate.delta)}</td></tr>
        </tbody>
      </table>
      <p className="small">
        Improved: {c.improved.length ? c.improved.map((x) => <code key={x}>{x.slice(-6)} </code>) : "none"} ·
        Regressed: {c.regressed.length ? c.regressed.map((x) => <code key={x} className="error-text">{x.slice(-6)} </code>) : "none"}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- release gates ----
export function Gates({ wfId }: { wfId: string }) {
  const qc = useQueryClient();
  const key = ["gates", wfId];
  const list = useQuery({ queryKey: key, queryFn: () => apiFetch<GateOut[]>(`/workflows/${wfId}/gates`) });
  const datasets = useQuery({ queryKey: ["datasets", wfId], queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${wfId}/datasets`) });
  const evaluators = useQuery({ queryKey: ["evaluators", wfId], queryFn: () => apiFetch<EvaluatorOut[]>(`/workflows/${wfId}/evaluators`) });
  const [environment, setEnvironment] = useState("prod");
  const [dataset, setDataset] = useState("");
  const [mins, setMins] = useState<Record<string, string>>({});
  const [regression, setRegression] = useState("");
  const save = useMutation({
    mutationFn: () => apiFetch<GateOut>(`/workflows/${wfId}/gates/${environment}`, {
      method: "PUT",
      json: {
        dataset_id: dataset || datasets.data?.[0]?.id,
        min_scores: Object.fromEntries(Object.entries(mins).filter(([, v]) => v.trim()).map(([k, v]) => [k, Number(v)])),
        max_regression: regression.trim() ? Number(regression) / 100 : null,
      },
    }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
  });
  const remove = useMutation({ mutationFn: (env: string) => apiFetch(`/workflows/${wfId}/gates/${env}`, { method: "DELETE" }), onSuccess: () => void qc.invalidateQueries({ queryKey: key }) });
  const name = (id: string) => datasets.data?.find((d) => d.id === id)?.name ?? id;
  return (
    <section className="pad">
      <p className="small muted">
        Publishing to a gated environment first evaluates the exact workflow on the dataset (reusing an experiment that already
        did) and is refused when a minimum mean score is missed or the pass rate drops more than allowed against the live version.
        Admins set gates and can publish over a failed one with a reason, which the audit log keeps. A gate keeps the dataset
        version and the evaluators as they were when it was saved: save it again to pick up later changes.
      </p>
      <QueryStatus q={list} what="release gates" empty={<p className="muted">No release gates: publishing needs no evaluation.</p>} />
      {list.data && list.data.length > 0 && (
        <table className="table" data-testid="gates">
          <thead><tr><th>Environment</th><th>Dataset</th><th>Minimum mean</th><th>Max drop vs live</th><th /></tr></thead>
          <tbody>
            {list.data.map((g) => (
              <tr key={g.id}>
                <td>{g.environment}</td>
                <td>{name(g.dataset_id)}{g.dataset_version != null && ` v${g.dataset_version}`}{g.evaluators && <span className="muted small"> · {g.evaluators.join(", ")}</span>}</td>
                <td className="small">{Object.entries(g.min_scores ?? {}).map(([k, v]) => `${k} ≥ ${v}`).join(", ") || "each evaluator's threshold"}</td>
                <td>{g.max_regression == null ? "—" : pct(g.max_regression)}</td>
                <td>{can("admin") && (
                  <button type="button" className="link small" onClick={() => {
                    if (window.confirm(`Remove the release gate on ${g.environment}? Publishing there will need no evaluation.`)) remove.mutate(g.environment);
                  }}>Remove</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {remove.error && <p role="alert" className="error-text">{errorText(remove.error)}</p>}
      {can("admin") && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }} data-testid="gate-form">
          <div className="row gap">
            <input aria-label="gate environment" value={environment} onChange={(e) => setEnvironment(e.target.value)} style={{ width: 110 }} />
            <select aria-label="gate dataset" value={dataset} onChange={(e) => setDataset(e.target.value)}>
              {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <input aria-label="max regression" placeholder="max drop in pass rate, %" value={regression} onChange={(e) => setRegression(e.target.value)} style={{ width: 190 }} />
            <button type="submit" disabled={save.isPending || !datasets.data?.length}>Save gate</button>
          </div>
          <div className="row gap small">
            Minimum mean (empty: the evaluator's threshold):
            {evaluators.data?.map((e) => (
              <label key={e.id} className="row">{e.name}
                <input aria-label={`minimum ${e.name}`} style={{ width: 60 }} value={mins[e.name] ?? ""} onChange={(x) => setMins((m) => ({ ...m, [e.name]: x.target.value }))} />
              </label>
            ))}
          </div>
          {save.error && <p role="alert" className="error-text">{errorText(save.error)}</p>}
        </form>
      )}
    </section>
  );
}
