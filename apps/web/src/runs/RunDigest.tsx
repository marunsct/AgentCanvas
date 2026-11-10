/** The weekly run digest (F-11) on the Runs page: last week's runs, why they failed, what they cost, how long they
 * took and the runs flagged most; or this week so far. Made by the server each Monday, without a model. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { DigestSummary, RunDigestOut } from "../api/generated";
import { PROJECT_ID } from "../api/queries";
import { QueryStatus } from "../ui/QueryStatus";

export function seconds(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60_000)} min`;
}

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "UTC" });

/** "Nov 2 – Nov 8" for a Monday-to-Monday week (the end is exclusive). */
export function weekLabel(d: Pick<RunDigestOut, "week_start" | "week_end" | "current">): string {
  if (d.current) return `This week so far (since ${day(d.week_start)})`;
  return `Week of ${day(d.week_start)} – ${day(new Date(new Date(d.week_end).getTime() - 86_400_000).toISOString())}`;
}

/** "+20%" against the week before; "" when there is nothing to compare. */
export function change(now: number, before: number | undefined): string {
  if (before === undefined || before === 0) return "";
  const p = Math.round(((now - before) / before) * 100);
  return p === 0 ? " (same as the week before)" : ` (${p > 0 ? "+" : ""}${p}% on the week before)`;
}

/** The summary to compare the newest kept digest with: the week right before it, when it has one. A week without
 * runs has no digest, and a part week (this week so far) is not compared with a whole one. */
export function weekBefore(list: RunDigestOut[]): DigestSummary | undefined {
  const [latest, prior] = list;
  return latest && prior && Date.parse(prior.week_end) === Date.parse(latest.week_start) ? prior.summary : undefined;
}

export function RunDigest() {
  const [current, setCurrent] = useState(false);
  const stored = useQuery({
    queryKey: ["run-digests", PROJECT_ID],
    queryFn: () => apiFetch<RunDigestOut[]>(`/projects/${PROJECT_ID}/run-digests?limit=2`),
  });
  const live = useQuery({
    queryKey: ["run-digest-current", PROJECT_ID],
    enabled: current,
    queryFn: () => apiFetch<RunDigestOut>(`/projects/${PROJECT_ID}/run-digests/current`),
  });
  const list = Array.isArray(stored.data) ? stored.data : [];
  const shown = current ? live.data : list[0];
  const before = current ? undefined : weekBefore(list);
  const s = shown?.summary;
  return (
    <details className="run-digest settings-card" aria-label="weekly digest">
      <summary>
        <strong>Weekly digest</strong>{" "}
        {s ? <span className="muted small">{weekLabel(shown)}: {s.runs} runs, {s.failed} failed, ${s.cost_usd.toFixed(2)}</span>
          : <span className="muted small">{stored.isLoading ? "…" : "none yet"}</span>}
      </summary>
      <div className="row gap small">
        <button type="button" className="link small" aria-pressed={!current} onClick={() => setCurrent(false)}>Last week</button>
        <button type="button" className="link small" aria-pressed={current} onClick={() => setCurrent(true)}>This week so far</button>
      </div>
      <QueryStatus q={current ? live : stored} what="the digest" />
      {!current && stored.data && list.length === 0 && (
        <p className="muted small">The digest appears here each Monday for the week before, once the project has runs.</p>
      )}
      {s && shown && (
        <div data-testid="run-digest">
          <p className="small">
            <strong>{weekLabel(shown)}:</strong> {s.runs} runs{change(s.runs, before?.runs)}, {s.failed} failed
            {s.failure_rate !== null ? ` (${Math.round(s.failure_rate * 100)}%)` : ""}, ${s.cost_usd.toFixed(2)} spent
            {change(s.cost_usd, before?.cost_usd)}. Took {seconds(s.latency_p50_ms)} typically, {seconds(s.latency_p95_ms)} at the 95th percentile.
          </p>
          {s.failure_groups.length > 0 && (
            <>
              <h4>Why runs failed</h4>
              <ul className="small" aria-label="failure groups">
                {s.failure_groups.map((g) => (
                  <li key={g.reason}>
                    <strong>{g.runs}×</strong> {g.reason} <span className="muted">({g.workflows.join(", ")})</span>{" "}
                    {g.example.workflow_id && <Link to={`/workflows/${g.example.workflow_id}?thread=${g.example.thread_id}`}>newest</Link>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {s.flagged.length > 0 && (
            <>
              <h4>Flagged most</h4>
              <ul className="small" aria-label="flagged runs">
                {s.flagged.map((f) => (
                  <li key={f.run_id}>
                    <Link to={`/workflows/${f.workflow_id}?thread=${f.thread_id}`}>{f.workflow_name}</Link> — {f.flags} flag{f.flags === 1 ? "" : "s"}: {f.why.join(", ")}
                  </li>
                ))}
              </ul>
            </>
          )}
          {s.workflows.length > 1 && (
            <>
              <h4>Busiest workflows</h4>
              <ul className="small" aria-label="busiest workflows">
                {s.workflows.map((w) => <li key={w.id}>{w.name}: {w.runs} runs, {w.failed} failed, ${w.cost_usd.toFixed(2)}</li>)}
              </ul>
            </>
          )}
        </div>
      )}
    </details>
  );
}
