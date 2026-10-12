/** Workflow-level settings shown when nothing is selected. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { SpendOut } from "../api/types";
import { useEditor } from "../editor/store";
import { FrameworkPicker } from "../editor/Frameworks";
import { MAX_ALERTS, parseAlertShares } from "./budget";
import { StateDesigner } from "./StateDesigner";

export function WorkflowPanel() {
  const wf = useEditor((s) => s.wf);
  const dispatch = useEditor((s) => s.dispatch);
  if (!wf) return null;
  const s = wf.settings;
  return (
    <div className="panel-body">
      <h3>Workflow</h3>
      <label>
        Name
        <input defaultValue={wf.name} key={`name-${wf.name}`}
          onBlur={(e) => e.target.value.trim() && e.target.value !== wf.name && dispatch([{ op: "UpdateMeta", name: e.target.value.trim() }])} />
      </label>
      <label>
        Description
        <textarea defaultValue={wf.description} rows={2} key={`desc-${wf.description}`}
          onBlur={(e) => e.target.value !== wf.description && dispatch([{ op: "UpdateMeta", description: e.target.value }])} />
      </label>
      <FrameworkPicker />
      <div className="row gap">
        <label>
          Recursion limit
          <input type="number" min={1} max={10000} value={s.recursion_limit ?? 50}
            onChange={(e) => dispatch([{ op: "UpdateSettings", patch: { recursion_limit: Number(e.target.value) || 50 } }], { debounceMs: 400 })} />
        </label>
        <label>
          Durability
          <select value={s.durability ?? "async"} onChange={(e) => dispatch([{ op: "UpdateSettings", patch: { durability: e.target.value } }])}>
            <option value="exit">exit (fastest)</option>
            <option value="async">async (default)</option>
            <option value="sync">sync (side effects)</option>
          </select>
        </label>
      </div>
      <BudgetEditor />
      <StateDesigner />
    </div>
  );
}

const BUDGET_FIELDS: [key: string, label: string, step: number][] = [
  ["max_tokens", "Tokens per run", 1],
  ["max_cost_usd", "USD per run", 0.01],
  ["max_seconds", "Seconds per run", 1],
  ["thread_max_tokens", "Tokens per conversation", 1],
  ["thread_max_cost_usd", "USD per conversation", 0.01],
  ["daily_max_cost_usd", "USD per day (all runs)", 0.01],
  ["monthly_max_cost_usd", "USD per month (all runs)", 0.01],
];


/** Budgets (FR-X-15): the runner stops a run that goes over; empty = no limit. */
function BudgetEditor() {
  const budget = useEditor((s) => s.wf?.settings.budget) as Record<string, number | null | undefined> | null | undefined;
  const alertAt = (budget as { alert_at?: number[] | null } | null | undefined)?.alert_at;
  const dispatch = useEditor((s) => s.dispatch);
  const set = (key: string, raw: string) => {
    const v = raw.trim() === "" ? null : Number(raw);
    if (v !== null && !(v > 0)) return;
    dispatch([{ op: "UpdateSettings", patch: { budget: { [key]: v } } }], { debounceMs: 400 });
  };
  return (
    <details className="budget" open={Boolean(budget && Object.values(budget).some((v) => v != null))}>
      <summary>Budget</summary>
      <div className="budget-grid">
        {BUDGET_FIELDS.map(([key, label, step]) => (
          <label key={key}>
            {label}
            <input type="number" min={step} step={step} placeholder="no limit" aria-label={`budget ${label}`}
              defaultValue={budget?.[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
          </label>
        ))}
        <label>
          Alert at (% of day or month, up to {MAX_ALERTS})
          <input placeholder="80" aria-label="budget alert at percent" title={`Up to ${MAX_ALERTS} percents, e.g. 50, 80`}
            defaultValue={alertAt ? alertAt.map((x) => Math.round(x * 100)).join(", ") : ""}
            onChange={(e) => {
              const shares = parseAlertShares(e.target.value);
              if (shares !== undefined) dispatch([{ op: "UpdateSettings", patch: { budget: { alert_at: shares } } }], { debounceMs: 400 });
            }} />
        </label>
      </div>
      <Spending />
    </details>
  );
}

const usd = (v: number) => `$${v < 0.01 && v > 0 ? v.toPrecision(2) : v.toFixed(2)}`;

/** S26: what the workflow's runs spent today and this month, against its day and month budgets, and its cost alerts. */
function Spending() {
  const workflowId = useEditor((s) => s.workflowId);
  const { data, refetch, isFetching } = useQuery({
    queryKey: ["spend", workflowId],
    queryFn: () => apiFetch<SpendOut>(`/workflows/${workflowId}/spend`),
    enabled: Boolean(workflowId),
  });
  if (!data) return null;
  const line = (p: SpendOut["day"], name: string) => {
    const share = p.limit_usd ? ` of ${usd(p.limit_usd)} (${Math.round((p.spent_usd / p.limit_usd) * 100)}%)` : "";
    const extras = [
      `${p.runs} run${p.runs === 1 ? "" : "s"}`,
      p.cache_hits ? `${p.cache_hits} cached answer${p.cache_hits === 1 ? "" : "s"}` : "",
      p.fallbacks ? `${p.fallbacks} answered by a backup model` : "",
    ].filter(Boolean).join(" · ");
    return <li data-testid={`spend-${p.period}`}><strong>{name}</strong> {usd(p.spent_usd)}{share} · {extras}</li>;
  };
  return (
    <div className="spending" aria-label="spending">
      <div className="row gap">
        <span className="small muted">Spending (UTC)</span>
        <button type="button" className="small" disabled={isFetching} onClick={() => void refetch()}>Refresh</button>
      </div>
      <ul className="small">{line(data.day, "Today")}{line(data.month, "This month")}</ul>
      {data.alerts.length > 0 && (
        <ul className="small cost-alerts" aria-label="cost alerts">
          {data.alerts.slice(0, 5).map((a) => (
            <li key={a.id} className={a.share >= 1 ? "error" : "warning"}>{a.message} · {new Date(a.created_at).toLocaleString()}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
