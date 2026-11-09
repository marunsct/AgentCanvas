/** The editor's Runs tab: this workflow's past conversations, newest first; one click opens it in the Timeline. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { ThreadPage } from "../api/generated";
import { useEditor } from "../editor/store";
import { useUi } from "../editor/ui";
import { duration } from "../runs/RunsPage";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";
import { useRun } from "./runStore";

const LABEL: Record<string, [string, string]> = {
  idle: ["done", "ok"], running: ["running", "status-running"], interrupted: ["waiting", "warn"], error: ["failed", "err"],
};

export function RunHistory() {
  const wfId = useEditor((s) => s.workflowId);
  const current = useRun((s) => s.threadId);
  const busy = useRun((s) => s.running || s.starting); // opening another thread now would mix the live run's events in
  const threads = useQuery({
    queryKey: ["threads", "editor", wfId], enabled: Boolean(wfId), refetchInterval: 10_000,
    queryFn: () => apiFetch<ThreadPage>(`/threads?workflow_id=${wfId}&limit=50`),
  });
  const items = Array.isArray(threads.data?.items) ? threads.data.items : [];
  return (
    <div className="pad run-history" data-testid="run-history">
      <QueryStatus q={threads} what="the runs" empty={<p className="muted small">No runs yet: press ▶ Test.</p>} />
      {items.length > 0 && (
        <table className="table compact">
          <tbody>
            {items.map((t) => {
              const [label, cls] = LABEL[t.status] ?? [t.status, ""];
              return (
                <tr key={t.id} className={`${busy ? "" : "clickable"}${t.id === current ? " selected" : ""}`}
                  aria-disabled={busy || undefined} title={busy ? "Wait for the current run to end (or stop it) to open another" : undefined}
                  onClick={() => { if (busy) return; useRun.getState().setThread(t.id); useUi.getState().setDock("timeline"); }}>
                  <td><span className={`badge ${cls}`}>{label}</span></td>
                  <td>{t.title ?? (t.deployment_id ? "API call" : "Test run")}
                    {t.last_error && <div className="error-text small">{t.last_error.slice(0, 100)}</div>}</td>
                  <td className="small muted" title={t.last_started_at ? exact(t.last_started_at) : undefined}>{t.last_started_at ? ago(t.last_started_at) : ""}</td>
                  <td className="small muted">{duration(t)}</td>
                  <td className="small muted">{t.deployment_id ? "API" : "Editor"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
