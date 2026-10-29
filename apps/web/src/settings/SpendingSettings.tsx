/** Settings › Organisation › Spending (admins): what the organisation's runs cost over a date range, in total and by
 * project, workflow and person, with tokens and answers taken from the cache. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { SpendPerson, SpendProject, SpendTotal, SpendWorkflow, SpendingOut } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";

const DAY = 86_400_000;
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const daysAgo = (n: number) => isoDay(new Date(Date.now() - n * DAY));

/** USD with the precision a small spender needs: a cent shows as $0.01, a tenth of a cent as $0.0010. */
export const usd = (v: number) => `$${v >= 1 ? v.toFixed(2) : v === 0 ? "0.00" : v.toFixed(4)}`;
const count = (n: number) => n.toLocaleString("en-US");

export function SpendingSettings() {
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(isoDay(new Date()));
  const valid = from !== "" && to !== "" && from <= to;
  const q = useQuery({
    queryKey: ["org-spending", from, to],
    enabled: valid,
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("since", new Date(`${from}T00:00:00`).toISOString());
      p.set("until", new Date(new Date(`${to}T00:00:00`).getTime() + DAY).toISOString()); // the whole last day
      return apiFetch<SpendingOut>(`/org/spending?${p}`);
    },
  });
  const preset = (days: number) => { setFrom(daysAgo(days - 1)); setTo(isoDay(new Date())); };
  return (
    <section className="settings-card" aria-label="Spending">
      <h2>Spending</h2>
      <p className="muted small">
        What this organisation's runs cost, by day of the range in your time zone. The cost counts models with a price
        (the catalog's, or <code>AGENTCANVAS_MODEL_PRICES</code>); a person is whoever started the conversation, and
        runs started by deployments, schedules and events belong to no one.
      </p>
      <div className="row gap wrap">
        <label className="row small">from <input type="date" aria-label="from date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="row small">to <input type="date" aria-label="to date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></label>
        {[7, 30, 90].map((n) => <button key={n} type="button" onClick={() => preset(n)}>Last {n} days</button>)}
      </div>
      {!valid && <p className="muted" role="status">Pick a start date that is not after the end date.</p>}
      {valid && <QueryStatus q={q} what="the spending report" />}
      {valid && q.data && (
        <>
          <Totals total={q.data.total} />
          <Breakdown title="By project" what="Project" rows={q.data.by_project} testid="spend-projects" />
          <Breakdown title="By workflow" what="Workflow" rows={q.data.by_workflow} testid="spend-workflows"
            extra={{ head: "Project id", cell: (r: SpendWorkflow) => r.project_id }} />
          <Breakdown title="By person" what="Person" rows={q.data.by_person} testid="spend-people"
            extra={{ head: "Email", cell: (r: SpendPerson) => r.email }} />
        </>
      )}
    </section>
  );
}

function Totals({ total }: { total: SpendTotal }) {
  return (
    <p data-testid="spend-total" className="row gap wrap">
      <strong>{usd(total.cost_usd)}</strong>
      <span>{count(total.runs)} runs</span>
      <span>{count(total.tokens_in + total.tokens_out)} tokens</span>
      <span>{count(total.cache_hits)} answers from the cache</span>
      <span>{count(total.fallbacks)} from backup models</span>
    </p>
  );
}

type Row = SpendProject | SpendWorkflow | SpendPerson;

function Breakdown<T extends Row>({ title, what, rows, testid, extra }: {
  title: string; what: string; rows: T[]; testid: string; extra?: { head: string; cell: (r: T) => React.ReactNode };
}) {
  return (
    <div>
      <h3>{title}</h3>
      {rows.length === 0 ? <p className="muted small">No runs in this range.</p> : (
        <table className="table" data-testid={testid}>
          <thead><tr>
            <th>{what}</th>{extra && <th>{extra.head}</th>}<th>Cost</th><th>Runs</th><th>Tokens in</th><th>Tokens out</th><th>Cached</th><th>Backup</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id ?? "none"}>
                <td>{r.name}</td>{extra && <td className="small muted">{extra.cell(r)}</td>}
                <td>{usd(r.cost_usd)}</td><td>{count(r.runs)}</td><td>{count(r.tokens_in)}</td><td>{count(r.tokens_out)}</td>
                <td>{count(r.cache_hits)}</td><td>{count(r.fallbacks)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
