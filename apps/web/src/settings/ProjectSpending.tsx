/** Settings › Project › Spending (E-9, E-10): this project's spending caps a UTC day and a calendar month, for its
 * admins, with what it spent and the organisation's caps that bind it too. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID, PROJECT_NAME } from "../api/queries";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { usd } from "./SpendingSettings";

type CapKey = "max_cost_usd_day" | "max_cost_usd_month";
interface CapUse { period: "day" | "month"; period_start: string; limit_usd: number | null; spent_usd: number }
// narrow local type until the generated API types are regenerated (GET /v1/projects/{id}/spending-caps)
export interface ProjectCapsOut { own: Record<CapKey, number | null>; organisation: Record<CapKey, number | null>; day: CapUse; month: CapUse }

const KEYS: { key: CapKey; use: "day" | "month"; label: string }[] = [
  { key: "max_cost_usd_day", use: "day", label: "Cap a day (USD)" },
  { key: "max_cost_usd_month", use: "month", label: "Cap a month (USD)" },
];

export function ProjectSpending() {
  const qc = useQueryClient();
  const key = ["project-spending-caps", PROJECT_ID];
  const q = useQuery({ queryKey: key, queryFn: () => apiFetch<ProjectCapsOut>(`/projects/${PROJECT_ID}/spending-caps`) });
  const [draft, setDraft] = useState<Record<CapKey, string>>({ max_cost_usd_day: "", max_cost_usd_month: "" });
  useEffect(() => {
    if (q.data) setDraft({ max_cost_usd_day: String(q.data.own.max_cost_usd_day ?? ""), max_cost_usd_month: String(q.data.own.max_cost_usd_month ?? "") });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch(`/projects/${PROJECT_ID}/spending-caps`, {
      method: "PUT",
      json: Object.fromEntries(KEYS.map(({ key: k }) => [k, draft[k].trim() === "" ? null : Number(draft[k])])),
    }),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: key }); toast(`Saved the spending caps of ${PROJECT_NAME}`); },
  });
  const data = q.data;
  return (
    <section className="settings-card" aria-label="Project spending caps">
      <h2>Spending caps</h2>
      <p className="muted small">
        The most this project may spend on models a UTC day and a calendar month, in USD: every run, the Copilot and AI
        judges. At a cap, its new runs are refused and its runs in flight stop until the day or month ends. A cap here
        cannot be above the organisation's, which binds the project too. Leave a field empty for no cap.
      </p>
      <QueryStatus q={q} what="the spending caps" />
      {data && (
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          {KEYS.map(({ key: k, use, label }) => (
            <label key={k} className="row gap small">
              {label}
              <input type="number" min={0} step="any" style={{ width: 120 }} aria-label={label} placeholder="no cap"
                max={data.organisation[k] ?? undefined} value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
              <span className="muted" data-testid={`project-spent-${use}`}>
                spent {usd(data[use].spent_usd)} {use === "day" ? "today" : "this month"}
                {data.organisation[k] != null && <> · the organisation's cap: {usd(data.organisation[k] ?? 0)}</>}
              </span>
            </label>
          ))}
          <button type="submit" className="primary" disabled={save.isPending}>Save</button>
          {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
        </form>
      )}
    </section>
  );
}
