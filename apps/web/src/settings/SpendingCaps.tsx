/** Settings › Organisation › Spending › Spending caps (admins): what the organisation and each project may spend a
 * UTC day and a calendar month, how much of it is used, and the latest cap alerts. At a cap, new runs (and Copilot
 * questions) are refused and runs in flight stop. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { CapUse, Period, ScopeCaps, SpendingCapsOut } from "../api/generated";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { usd } from "./SpendingSettings";

type CapKey = "max_cost_usd_day" | "max_cost_usd_month";
const KEYS: { key: CapKey; period: Period; label: string }[] = [
  { key: "max_cost_usd_day", period: "day", label: "a day" },
  { key: "max_cost_usd_month", period: "month", label: "a month" },
];
type Draft = Record<string, string>; // what is typed, per scope and key ("" = no cap of its own)

const QK = ["org-spending-caps"];
const field = (scope: string, key: string) => `${scope}:${key}`;
const typed = (v: number | null | undefined) => (v == null ? "" : String(v));

function draftOf(data: SpendingCapsOut): Draft {
  const out: Draft = { "org:cost_warn_percent": "" };
  for (const s of [data.organisation, ...data.projects]) {
    for (const { key } of KEYS) out[field(s.id ?? "org", key)] = typed(s.own[key]);
  }
  return out;
}

/** "$3.20 of $10.00 (32%)", or what was spent when there is no cap. */
function Used({ use, testid }: { use: CapUse; testid: string }) {
  if (use.limit_usd == null) return <span data-testid={testid} className="muted">{usd(use.spent_usd)} (no cap)</span>;
  const share = use.spent_usd / use.limit_usd;
  return (
    <span data-testid={testid} className={share >= 1 ? "error-text" : undefined}>
      {usd(use.spent_usd)} of {usd(use.limit_usd)} ({Math.min(999, Math.round(share * 100))}%)
      <meter aria-label={`used of the ${use.period} cap`} min={0} max={1} low={0.8} high={0.99} optimum={0} value={Math.min(1, share)} style={{ display: "block", width: 140 }} />
    </span>
  );
}

export function SpendingCaps() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: QK, queryFn: () => apiFetch<SpendingCapsOut>("/org/spending/caps") });
  const [draft, setDraft] = useState<Draft>({});
  useEffect(() => { if (q.data) setDraft(draftOf(q.data)); }, [q.data]);
  const save = useMutation({
    mutationFn: async (scope: ScopeCaps) => {
      const sid = scope.id ?? "org";
      const values: Record<string, number | null> = {};
      for (const { key } of KEYS) {
        const raw = (draft[field(sid, key)] ?? "").trim();
        values[key] = raw === "" ? null : Number(raw);
      }
      if (scope.id === null) {
        const warn = (draft["org:cost_warn_percent"] ?? "").trim();
        if (warn !== "") values.cost_warn_percent = Number(warn);
        await apiFetch("/org/settings", { method: "PUT", json: { limits: values } });
      } else {
        await apiFetch(`/projects/${encodeURIComponent(scope.id)}/spending-caps`, { method: "PUT", json: values });
      }
      return scope.name;
    },
    onSuccess: async (name) => {
      await qc.invalidateQueries({ queryKey: QK });
      void qc.invalidateQueries({ queryKey: ["org-settings"] });
      toast(`Saved the spending caps of ${name}`);
    },
  });
  const set = (key: string, v: string) => setDraft({ ...draft, [key]: v });
  const data = q.data;
  return (
    <section className="settings-card" aria-label="Spending caps">
      <h2>Spending caps</h2>
      <p className="muted small">
        The most the organisation, or one project, may spend on models a UTC day and a calendar month, in USD: every
        run (drafts, debug runs, experiments, deployments, schedules and events), the Copilot and AI judges. At a cap,
        new runs are refused and runs in flight stop until the day or month ends, or until the cap is raised. An alert
        is raised when spending reaches the alert share and when it reaches the cap. A project's cap cannot be above
        the organisation's. Leave a field empty for no cap.
      </p>
      <QueryStatus q={q} what="the spending caps" />
      {data && (
        <>
          <table className="table" data-testid="spend-caps">
            <thead><tr><th>Scope</th><th>Cap a day</th><th>Cap a month</th><th>Today</th><th>This month</th><th /></tr></thead>
            <tbody>
              {[data.organisation, ...data.projects].map((s) => {
                const sid = s.id ?? "org";
                return (
                  <tr key={sid}>
                    <td>{s.id === null ? <strong>{s.name} (the organisation)</strong> : s.name}</td>
                    {KEYS.map(({ key, label }) => (
                      <td key={key}>
                        <input type="number" min={0} step="any" style={{ width: 110 }}
                          aria-label={`${s.id === null ? "Organisation" : s.name}: cap ${label}`}
                          placeholder={s.id === null && data.server[key] > 0 ? `server: ${data.server[key]}` : "no cap"}
                          max={s.id === null && data.server[key] > 0 ? data.server[key] : undefined}
                          value={draft[field(sid, key)] ?? ""} onChange={(e) => set(field(sid, key), e.target.value)} />
                      </td>
                    ))}
                    <td><Used use={s.day} testid={`spent-day-${sid}`} /></td>
                    <td><Used use={s.month} testid={`spent-month-${sid}`} /></td>
                    <td>
                      <button type="button" disabled={save.isPending} onClick={() => save.mutate(s)}>Save</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <label className="row small gap">
            Alert at
            <input type="number" min={1} max={100} style={{ width: 70 }} aria-label="Alert at percent of a cap"
              placeholder={String(data.warn_percent)} value={draft["org:cost_warn_percent"] ?? ""}
              onChange={(e) => set("org:cost_warn_percent", e.target.value)} />
            % of a cap (saved with the organisation's caps)
          </label>
          {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
          <h3>Latest cap alerts</h3>
          {data.alerts.length === 0 ? <p className="muted small">No spending cap was reached or nearly reached yet.</p> : (
            <ul data-testid="cap-alerts" className="small">
              {data.alerts.map((a) => (
                <li key={`${a.project_id ?? "org"}:${a.period_start}:${a.share}`}>
                  <span className="muted">{new Date(a.created_at).toLocaleString()}</span> {a.message}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
