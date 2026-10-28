/** Inbox (M4-T4 UI): every pending approval/input across workflows; deciding resumes the run in the background. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { useInbox } from "../api/queries";
import { can } from "../api/session";
import { DecisionCard } from "./DecisionCard";
import { QueryStatus } from "../ui/QueryStatus";
import { PageHeader } from "../ui/PageHeader";
import { toast } from "../ui/dialog";
import { ago, exact } from "../ui/time";
import { useState } from "react";

export function InboxPage() {
  const inbox = useInbox();
  const [only, setOnly] = useState("");
  const names = [...new Map((inbox.data ?? []).filter((i) => i.workflow_id).map((i) => [i.workflow_id!, i.workflow_name ?? "Workflow"])).entries()];
  const shown = (inbox.data ?? []).filter((i) => !only || i.workflow_id === only);
  const qc = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ id, value }: { id: string; value: unknown }) =>
      apiFetch(`/inbox/${id}/decision`, { method: "POST", json: { value } }),
    onSuccess: () => {
      toast("Decided: the run continues");
      setTimeout(() => void qc.invalidateQueries({ queryKey: ["inbox"] }), 300);
    },
  });
  return (
    <div className="page">
      <PageHeader title="Inbox" description="Runs waiting for a person: an approval, an edit or an answer. Deciding resumes the run." />
      <QueryStatus q={inbox} what="the inbox" />
      {inbox.data?.length === 0 && <p className="muted" data-testid="inbox-empty">Nothing is waiting for you.</p>}
      {names.length > 1 && (
        <div className="row gap list-tools">
          <select aria-label="filter by workflow" value={only} onChange={(e) => setOnly(e.target.value)}>
            <option value="">Every workflow ({inbox.data!.length})</option>
            {names.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </div>
      )}
      <div className="inbox-list">
        {shown.map((item) => (
          <article key={item.id} className="inbox-item" data-testid="inbox-item">
            <header className="row gap">
              {item.workflow_id ? <Link to={`/workflows/${item.workflow_id}`}>{item.workflow_name}</Link> : <span>A deleted workflow</span>}
              <small className="muted">
                at <b>{item.node_label ?? "a step"}</b> · <span title={exact(item.created_at)}>{ago(item.created_at)}</span>
                {item.workflow_id && <> · <Link to={`/workflows/${item.workflow_id}?thread=${item.thread_id}`}>see the run</Link></>}
              </small>
            </header>
            <DecisionCard intr={item} busy={decide.isPending || !can("reviewer")} onDecide={(value) => decide.mutate({ id: item.id, value })} />
          </article>
        ))}
      </div>
      {decide.error && <p className="error-text">{(decide.error as Error).message}</p>}
    </div>
  );
}
