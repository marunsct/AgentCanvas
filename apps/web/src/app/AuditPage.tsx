/** Audit log (M7-T4): who changed what and when, newest first. Admins only. Filter by action, target and dates;
 * open an entry for its details; export what is loaded as CSV. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import { apiFetch } from "../api/client";
import type { AuditOut as Entry } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";

const PAGE = 100;

/** `put_secret` → "Put secret": the route names the log records, in words. */
export const actionLabel = (a: string) => (a.charAt(0).toUpperCase() + a.slice(1)).replace(/_/g, " ");

export function toCsv(rows: Entry[]): string {
  const cell = (v: unknown) => {
    const t = v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  const head = ["time", "who", "action", "target", "ip", "detail"];
  return [head.join(","), ...rows.map((e) =>
    [e.created_at, e.actor_email ?? e.actor_id ?? "", e.action, e.target, e.ip ?? "", e.detail].map(cell).join(","))].join("\n") + "\n";
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AuditPage() {
  const [action, setAction] = useState("");
  const [target, setTarget] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const log = useInfiniteQuery({
    queryKey: ["audit", action, target, from, to],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: String(PAGE) });
      if (action.trim()) q.set("action", action.trim());
      if (target.trim()) q.set("target", target.trim());
      if (from) q.set("since", new Date(`${from}T00:00:00`).toISOString());
      if (to) q.set("until", new Date(new Date(`${to}T00:00:00`).getTime() + 86_400_000).toISOString()); // the whole day
      if (pageParam) q.set("before", String(pageParam));
      return apiFetch<Entry[]>(`/org/audit?${q}`);
    },
    getNextPageParam: (last) => (Array.isArray(last) && last.length === PAGE ? last[last.length - 1].id : undefined),
  });
  const rows = useMemo(() => (log.data?.pages ?? []).flatMap((p) => (Array.isArray(p) ? p : [])), [log.data]);
  const seen = useMemo(() => [...new Set(rows.map((e) => e.action))].sort(), [rows]);
  return (
    <section className="settings-card audit">
      <h2>Audit log</h2>
      <p className="muted small">Who changed what in this organisation, newest first.</p>
      <div className="row gap wrap audit-filters">
        <input placeholder="action, e.g. publish" value={action} onChange={(e) => setAction(e.target.value)} aria-label="action" list="audit-actions" />
        <datalist id="audit-actions">{seen.map((a) => <option key={a} value={a}>{actionLabel(a)}</option>)}</datalist>
        <input placeholder="target contains, e.g. a workflow id" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="target" />
        <label className="row small">from <input type="date" aria-label="from date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="row small">to <input type="date" aria-label="to date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <button type="button" disabled={rows.length === 0} onClick={() => download(`audit-${new Date().toISOString().slice(0, 10)}.csv`, toCsv(rows))}
          title="the entries loaded below">Export CSV</button>
      </div>
      <QueryStatus q={log} what="the audit log" />
      {log.isSuccess && rows.length === 0 && <p className="muted">Nothing recorded{action || target || from || to ? " for this filter" : " yet"}.</p>}
      {rows.length > 0 && (
        <table className="table audit" data-testid="audit">
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>From</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <Fragment key={e.id}>
                <tr className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                  <td className="small muted" title={exact(e.created_at)}>
                    <button type="button" className="link small" aria-expanded={open === e.id} aria-label={`details of the change ${ago(e.created_at)}`}
                      onClick={(ev) => { ev.stopPropagation(); setOpen(open === e.id ? null : e.id); }}>{ago(e.created_at)}</button>
                  </td>
                  <td>{e.actor_email ?? (e.actor_id ? e.actor_id : <span className="muted">{String(e.detail.caller ?? "system")}</span>)}</td>
                  <td title={e.action}>{actionLabel(e.action)}</td>
                  <td className="small">{e.target}</td>
                  <td className="small muted">{e.ip}</td>
                </tr>
                {open === e.id && (
                  <tr className="detail-row"><td colSpan={5}><pre className="json small">{JSON.stringify({ at: e.created_at, ...e.detail }, null, 2)}</pre></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {log.hasNextPage && <button type="button" onClick={() => void log.fetchNextPage()} disabled={log.isFetchingNextPage}>Load older</button>}
    </section>
  );
}
