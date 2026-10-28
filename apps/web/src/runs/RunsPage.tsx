/** Runs: the project's conversations (threads), newest first, with status, where they came from, duration and cost;
 * open one in the editor's timeline, or delete it. */
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { ThreadOut, ThreadPage } from "../api/generated";
import { PROJECT_ID, useWorkflows } from "../api/queries";
import { useCan } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { PageHeader } from "../ui/PageHeader";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";

const STATUS: Record<string, { label: string; cls: string }> = {
  idle: { label: "done", cls: "ok" },
  running: { label: "running", cls: "status-running" },
  interrupted: { label: "waiting for a person", cls: "warn" },
  error: { label: "failed", cls: "err" },
};

export function duration(t: Pick<ThreadOut, "last_started_at" | "last_ended_at">): string {
  if (!t.last_started_at || !t.last_ended_at) return "";
  const ms = new Date(t.last_ended_at).getTime() - new Date(t.last_started_at).getTime();
  if (!(ms >= 0)) return "";
  return ms < 1000 ? `${ms} ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60_000)} min`;
}

export function RunsPage() {
  const [status, setStatus] = useState("");
  const [workflow, setWorkflow] = useState("");
  const workflows = useWorkflows();
  const canBuild = useCan("builder");
  const qc = useQueryClient();
  const threads = useInfiniteQuery({
    queryKey: ["threads", PROJECT_ID, status, workflow],
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ project_id: PROJECT_ID, limit: "50" });
      if (status) q.set("status", status);
      if (workflow) q.set("workflow_id", workflow);
      if (pageParam) q.set("cursor", pageParam);
      return apiFetch<ThreadPage>(`/threads?${q}`);
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/threads/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast("Deleted the conversation"); void qc.invalidateQueries({ queryKey: ["threads"] }); },
  });
  const items = threads.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="page runs-page">
      <PageHeader title="Runs" description="Every conversation with this project's workflows: from the editor, the API, triggers and share links." />
      <div className="row gap wrap list-tools">
        <select aria-label="filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select aria-label="filter by workflow" value={workflow} onChange={(e) => setWorkflow(e.target.value)}>
          <option value="">Every workflow</option>
          {workflows.data?.items.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      </div>
      <QueryStatus q={threads} what="the runs" />
      {threads.data && items.length === 0 && (
        <div className="empty-state">{status || workflow ? "No runs match these filters." : "No runs yet. Run a workflow from the editor with ▶ Test."}</div>
      )}
      {items.length > 0 && (
        <table className="table runs-table" data-testid="runs">
          <thead><tr><th>Status</th><th>Workflow</th><th>From</th><th>Started</th><th>Took</th><th>Runs</th><th>Cost</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {items.map((t) => {
              const s = STATUS[t.status] ?? { label: t.status, cls: "" };
              return (
                <tr key={t.id}>
                  <td><span className={`badge ${s.cls}`}>{s.label}</span></td>
                  <td>
                    <Link to={`/workflows/${t.workflow_id}?thread=${t.id}`} title="Open in the editor's timeline">{t.workflow_name ?? t.workflow_id}</Link>
                    {t.title && <div className="muted small">{t.title}</div>}
                    {t.last_error && <div className="error-text small" title={t.last_error}>{t.last_error.slice(0, 120)}</div>}
                  </td>
                  <td className="small">{t.deployment_id ? "API" : "Editor"}</td>
                  <td className="small" title={t.last_started_at ? exact(t.last_started_at) : undefined}>{t.last_started_at ? ago(t.last_started_at) : "—"}</td>
                  <td className="small">{duration(t) || "—"}</td>
                  <td className="small">{t.runs}</td>
                  <td className="small">{t.cost_usd ? `$${t.cost_usd.toFixed(4)}` : "—"}</td>
                  <td className="actions">{canBuild && (
                    <button type="button" className="link small" aria-label={`delete conversation ${t.id}`} disabled={remove.isPending}
                      onClick={async () => {
                        if (await confirmDialog({ title: "Delete this conversation?", body: "Its runs, checkpoints and pending decisions are deleted.", confirm: "Delete", danger: true })) remove.mutate(t.id);
                      }}>Delete</button>
                  )}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {remove.error && <p className="error-text small" role="alert">Could not delete: {(remove.error as Error).message}</p>}
      {threads.hasNextPage && (
        <button type="button" disabled={threads.isFetchingNextPage} onClick={() => void threads.fetchNextPage()}>
          {threads.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
