/** Workflow-level settings shown when nothing is selected. */
import { useEditor } from "../editor/store";
import { FrameworkPicker } from "../editor/Frameworks";
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
];

/** Budgets (FR-X-15): the runner stops a run that goes over; empty = no limit. */
function BudgetEditor() {
  const budget = useEditor((s) => s.wf?.settings.budget) as Record<string, number | null | undefined> | null | undefined;
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
      </div>
    </details>
  );
}
