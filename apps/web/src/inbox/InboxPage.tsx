/** Inbox (M4-T4 UI): every pending approval/input across workflows; deciding resumes the run in the background. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { DecisionCard } from "./DecisionCard";

export interface InboxItem {
  id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  thread_id: string;
  canvas_node_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export function useInbox() {
  return useQuery({ queryKey: ["inbox"], queryFn: () => apiFetch<InboxItem[]>("/inbox"), refetchInterval: 5000 });
}

export function InboxPage() {
  const inbox = useInbox();
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ id, value }: { id: string; value: unknown }) =>
      apiFetch(`/inbox/${id}/decision`, { method: "POST", json: { value } }),
    onSuccess: () => setTimeout(() => void qc.invalidateQueries({ queryKey: ["inbox"] }), 300),
  });
  return (
    <div className="page">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Inbox</span></header>
      <h2>Pending decisions</h2>
      {inbox.isLoading && <p className="muted">Loading…</p>}
      {inbox.data?.length === 0 && <p className="muted" data-testid="inbox-empty">Nothing is waiting for you.</p>}
      <div className="inbox-list">
        {inbox.data?.map((item) => (
          <article key={item.id} className="inbox-item" data-testid="inbox-item">
            <header className="row gap">
              {item.workflow_id ? <Link to={`/workflows/${item.workflow_id}`}>{item.workflow_name}</Link> : <span>unknown workflow</span>}
              <small className="muted">node {item.canvas_node_id} · thread {item.thread_id} · {new Date(item.created_at).toLocaleString()}</small>
            </header>
            <DecisionCard intr={item} busy={decide.isPending} onDecide={(value) => decide.mutate({ id: item.id, value })} />
          </article>
        ))}
      </div>
      {decide.error && <p className="error-text">{(decide.error as Error).message}</p>}
    </div>
  );
}
