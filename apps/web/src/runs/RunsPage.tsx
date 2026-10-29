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
import { Badge, type Tone } from "../ui/Badge";
import { EmptyState } from "../ui/EmptyState";
import { Table } from "../ui/Table";
import { AddToDataset } from "./AddToDataset";
import { sinceFor, WHEN, type When } from "./runs";
import { Activity } from "lucide-react";

const STATUS: Record<string, { label: string; tone: Tone }> = {
  idle: { label: "done", tone: "ok" },
  running: { label: "running", tone: "running" },
  interrupted: { label: "waiting for a person", tone: "warn" },
  error: { label: "failed", tone: "err" },
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
  const [when, setWhen] = useState<When>("");
  const workflows = useWorkflows();
  const canBuild = useCan("builder");
  const qc = useQueryClient();
  const [picked, setPicked] = useState<string[]>([]); // conversations ticked to become dataset examples
  const threads = useInfiniteQuery({
    queryKey: ["threads", PROJECT_ID, status, workflow, when],
    initialPageParam: "",
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ project_id: PROJECT_ID, limit: "50" });
      if (status) q.set("status", status);
      if (workflow) q.set("workflow_id", workflow);
      const since = sinceFor(when);
      if (since) q.set("since", since);
      if (pageParam) q.set("cursor", pageParam);
      return apiFetch<ThreadPage>(`/threads?${q}`);
    },
    getNextPageParam: (last) => last?.next_cursor ?? undefined,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/threads/${id}`, { method: "DELETE" }),
    onSuccess: () => { toast("Deleted the conversation"); void qc.invalidateQueries({ queryKey: ["threads"] }); },
  });
  // a page of another shape (a server of another version) shows as nothing, not a crash
  const items = threads.data?.pages.flatMap((p) => (Array.isArray(p?.items) ? p.items : [])) ?? [];
  const chosen = items.filter((t) => picked.includes(t.id));
  const pick = (id: string, on: boolean) => setPicked((p) => (on ? [...p, id] : p.filter((x) => x !== id)));
  return (
    <div className="page runs-page">
      <PageHeader title="Runs" description="Every conversation with this project's workflows: from the editor, the API, triggers and share links." />
      <div className="row gap wrap list-tools">
        <select aria-label="filter by status" value={status} onChange={(e) => { setStatus(e.target.value); setPicked([]); }}>
          <option value="">Any status</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select aria-label="filter by workflow" value={workflow} onChange={(e) => { setWorkflow(e.target.value); setPicked([]); }}>
          <option value="">Every workflow</option>
          {workflows.data?.items.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <select aria-label="filter by date" value={when} onChange={(e) => { setWhen(e.target.value as When); setPicked([]); }}>
          {WHEN.map((w) => <option key={w.id} value={w.id}>{w.id ? `Started: ${w.label.toLowerCase()}` : "Started: any time"}</option>)}
        </select>
      </div>
      {canBuild && chosen.length > 0 && <AddToDataset threads={chosen} onDone={() => setPicked([])} />}
      <QueryStatus q={threads} what="the runs" />
      {threads.data && items.length === 0 && (
        status || workflow || when
          ? <div className="empty-state">No runs match these filters.</div>
          : <EmptyState icon={Activity} title="No runs yet">Run a workflow from the editor with ▶ Test, or call a published one; every conversation shows here.</EmptyState>
      )}
      {items.length > 0 && (
        <Table className="runs-table" testId="runs" columns={[
          ...(canBuild ? [{ label: "Select", hidden: true }] : []),
          { label: "Status" }, { label: "Workflow" }, { label: "From" }, { label: "Started" }, { label: "Took" }, { label: "Runs" },
          { label: "Cost" }, { label: "Actions", hidden: true },
        ]}>
          {items.map((t) => {
            const s = STATUS[t.status] ?? { label: t.status, tone: "neutral" as Tone };
            return (
              <tr key={t.id}>
                {canBuild && <td><input type="checkbox" aria-label={`select conversation ${t.id}`} checked={picked.includes(t.id)}
                  onChange={(e) => pick(t.id, e.target.checked)} /></td>}
                <td><Badge tone={s.tone}>{s.label}</Badge></td>
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
        </Table>
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
