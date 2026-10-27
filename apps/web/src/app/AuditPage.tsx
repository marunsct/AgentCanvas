/** Audit log (M7-T4): who changed what and when, newest first. Admins only. */
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { QueryStatus } from "../ui/QueryStatus";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import type { AuditOut as Entry } from "../api/generated";


const PAGE = 100;

export function AuditPage() {
  const [action, setAction] = useState("");
  const [target, setTarget] = useState("");
  const log = useInfiniteQuery({
    queryKey: ["audit", action, target],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: String(PAGE) });
      if (action.trim()) q.set("action", action.trim());
      if (target.trim()) q.set("target", target.trim());
      if (pageParam) q.set("before", String(pageParam));
      return apiFetch<Entry[]>(`/org/audit?${q}`);
    },
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].id : undefined),
  });
  const rows = log.data?.pages.flat() ?? [];
  return (
    <div className="page">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Audit log</span><span className="spacer" /><ThemeSwitcher /></header>
      <div className="row gap">
        <input placeholder="action, e.g. publish" value={action} onChange={(e) => setAction(e.target.value)} aria-label="action" />
        <input placeholder="target contains, e.g. a workflow id" value={target} onChange={(e) => setTarget(e.target.value)} aria-label="target" />
      </div>
      <QueryStatus q={log} what="the audit log" />
      {log.isSuccess && rows.length === 0 && <p className="muted">Nothing recorded yet{action || target ? " for this filter" : ""}.</p>}
      <table className="audit" data-testid="audit">
        <tbody>
          {rows.map((e) => (
            <tr key={e.id}>
              <td className="small muted">{new Date(e.created_at).toLocaleString()}</td>
              <td>{e.actor_email ?? (e.actor_id ? e.actor_id : <span className="muted">{String(e.detail.caller ?? "system")}</span>)}</td>
              <td><code>{e.action}</code></td>
              <td className="small">{e.target}</td>
              <td className="small muted">{e.ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {log.hasNextPage && <button type="button" onClick={() => void log.fetchNextPage()} disabled={log.isFetchingNextPage}>Load older</button>}
    </div>
  );
}
