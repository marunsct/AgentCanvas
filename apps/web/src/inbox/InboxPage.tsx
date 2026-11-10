/** Inbox (M4-T4 UI): every pending approval/input across workflows; deciding resumes the run in the background. */
import { useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { apiFetch } from "../api/client";
import { type InboxItem, PROJECT_ID, useInbox } from "../api/queries";
import { can } from "../api/session";
import { DecisionCard } from "./DecisionCard";
import { QueryStatus } from "../ui/QueryStatus";
import { PageHeader } from "../ui/PageHeader";
import { toast } from "../ui/dialog";
import { ago, exact } from "../ui/time";
import { useEffect, useRef, useState } from "react";
import { EmptyState } from "../ui/EmptyState";
import { Inbox } from "lucide-react";

export function InboxPage() {
  const inbox = useInbox();
  const [only, setOnly] = useState("");
  const names = [...new Map((inbox.data ?? []).filter((i) => i.workflow_id).map((i) => [i.workflow_id!, i.workflow_name ?? "Workflow"])).entries()];
  const shown = (inbox.data ?? []).filter((i) => !only || i.workflow_id === only);
  // a notification's link names the item: it is shown first in view, or said to be gone
  const [params] = useSearchParams();
  const linked = params.get("item");
  const gone = Boolean(linked && inbox.data && !inbox.data.some((i) => i.id === linked));
  useEffect(() => {
    if (linked && inbox.data?.some((i) => i.id === linked)) document.getElementById(`inbox-${linked}`)?.scrollIntoView?.({ block: "center" });
  }, [linked, inbox.data]);
  const qc = useQueryClient();
  // per item: a decided item used to stay clickable until the list reloaded (a second click: a 409), one decision
  // in flight disabled every card, and one failure was shown under all of them
  const deciding = useRef(new Set<string>());
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const mark = (id: string, on: boolean) => {
    if (on) deciding.current.add(id);
    else deciding.current.delete(id);
    setBusy(new Set(deciding.current));
  };
  const decide = async (id: string, value: unknown) => {
    if (deciding.current.has(id)) return;
    mark(id, true);
    setErrors((all) => Object.fromEntries(Object.entries(all).filter(([k]) => k !== id)));
    try {
      await apiFetch(`/inbox/${id}/decision`, { method: "POST", json: { value } });
      qc.setQueryData<InboxItem[]>(["inbox"], (items) => items?.filter((i) => i.id !== id));
      toast("Decided: the run continues");
      setTimeout(() => void qc.invalidateQueries({ queryKey: ["inbox"] }), 300);
    } catch (e) {
      setErrors((all) => ({ ...all, [id]: (e as Error).message }));
    } finally {
      mark(id, false);
    }
  };
  const stop = async (item: InboxItem) => {
    mark(item.id, true);
    try {
      await apiFetch(`/runs/${item.run_id}/cancel`, { method: "POST" });
      qc.setQueryData<InboxItem[]>(["inbox"], (items) => items?.filter((i) => i.id !== item.id));
      toast("Stopped: the run's wait was cancelled");
    } catch (e) {
      setErrors((all) => ({ ...all, [item.id]: (e as Error).message }));
    } finally {
      mark(item.id, false);
    }
  };
  return (
    <div className="page">
      <PageHeader title="Inbox" description="Runs waiting for a person, in every project: an approval, an edit or an answer. Deciding resumes the run. Runs at a Wait step show here too; they continue on their own." />
      <QueryStatus q={inbox} what="the inbox" />
      {gone && <p className="callout small" data-testid="inbox-gone">That item is no longer waiting: someone decided it, or it expired.</p>}
      {inbox.data?.length === 0 && (
        <EmptyState icon={Inbox} title="Nothing is waiting for you" testId="inbox-empty">
          When a run pauses for an approval or an answer, it shows here for anyone with the reviewer role.
        </EmptyState>
      )}
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
          <article key={item.id} id={`inbox-${item.id}`} className={item.id === linked ? "inbox-item linked" : "inbox-item"}
            data-testid="inbox-item" aria-current={item.id === linked ? "true" : undefined}>
            <header className="row gap">
              {item.workflow_id ? <Link to={`/workflows/${item.workflow_id}`}>{item.workflow_name}</Link> : <span>A deleted workflow</span>}
              {item.project_id && item.project_id !== PROJECT_ID && (
                <span className="badge" data-testid="inbox-project" title="The Inbox shows every project; opening it switches to this one">
                  {item.project_name}</span>
              )}
              <small className="muted">
                at <b>{item.node_label ?? "a step"}</b> · <span title={exact(item.created_at)}>{ago(item.created_at)}</span>
                {item.workflow_id && <> · <Link to={`/workflows/${item.workflow_id}?thread=${item.thread_id}`}>see the run</Link></>}
              </small>
            </header>
            {item.deciders && <p className="small muted" data-testid="inbox-deciders">Decided by: {item.deciders}</p>}
            {item.may_decide === false && item.why_not && item.kind !== "timer" && (
              <p className="small muted" data-testid="inbox-why-not">You cannot decide this one: {item.why_not}.</p>
            )}
            <DecisionCard intr={item}
              busy={busy.has(item.id) || (item.kind !== "timer"
                && (item.may_decide === false || (item.may_decide == null && !can("reviewer"))))}
              onDecide={(value) => void decide(item.id, value)}
              onStop={item.kind === "timer" && can("builder") ? () => void stop(item) : undefined} />
            {errors[item.id] && <p className="error-text" role="alert">Could not decide: {errors[item.id]}</p>}
          </article>
        ))}
      </div>
    </div>
  );
}
