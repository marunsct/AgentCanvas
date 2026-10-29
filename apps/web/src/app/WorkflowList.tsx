/** Home's list of the project's workflows: a compact table, five at a time, found by name, filtered by when it
 * changed, sorted; per workflow open, rename, duplicate or delete. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, LayoutGrid, MoreHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { useWorkflows } from "../api/queries";
import { useCan } from "../api/session";
import { confirmDialog, promptDialog, toast } from "../ui/dialog";
import { Menu } from "../ui/Menu";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";
import { EmptyState } from "../ui/EmptyState";
import { CHANGED, type Changed, listPage, pageNumbers, type Sort } from "./workflowList";

export function WorkflowList() {
  const workflows = useWorkflows();
  const canBuild = useCan("builder");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("updated");
  const [changed, setChanged] = useState<Changed>("any");
  const [page, setPage] = useState(1);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["workflows"] });
  const act = useMutation({
    mutationFn: async ({ kind, id, name }: { kind: "rename" | "duplicate" | "delete"; id: string; name?: string }) => {
      if (kind === "rename") return apiFetch(`/workflows/${id}`, { method: "PATCH", json: { name } });
      if (kind === "duplicate") return apiFetch<{ id: string }>(`/workflows/${id}/duplicate`, { method: "POST", json: {} });
      return apiFetch(`/workflows/${id}`, { method: "DELETE" });
    },
    onSuccess: (out, { kind, name }) => {
      refresh();
      if (kind === "rename") toast(`Renamed to ${name}`);
      if (kind === "delete") toast(`Deleted ${name}`);
      if (kind === "duplicate") { toast("Duplicated"); navigate(`/workflows/${(out as { id: string }).id}`); }
    },
  });
  const view = useMemo(() => listPage(workflows.data?.items ?? [], { q, changed, sort, page }), [workflows.data, q, changed, sort, page]);
  const filtered = q.trim() !== "" || changed !== "any";

  return (
    <section aria-label="Workflows">
      <div className="row gap list-tools">
        <input type="search" placeholder="Search workflows" aria-label="search workflows" value={q}
          onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <select aria-label="changed" value={changed} onChange={(e) => { setChanged(e.target.value as Changed); setPage(1); }}>
          {CHANGED.map((c) => <option key={c.id} value={c.id}>{c.id === "any" ? "Changed: any time" : c.label}</option>)}
        </select>
        <select aria-label="sort workflows" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
          <option value="updated">Last changed</option>
          <option value="name">Name</option>
        </select>
        <span className="spacer" />
        {workflows.data && <span className="muted small" data-testid="workflow-count">
          {filtered ? `${view.total} of ${workflows.data.items.length}` : view.total} workflow{view.total === 1 ? "" : "s"}</span>}
      </div>
      <QueryStatus q={workflows} what="workflows" />
      {workflows.data?.items.length === 0 && (
        <EmptyState icon={LayoutGrid} title="No workflows yet">Start from a template below, or with an empty canvas.</EmptyState>
      )}
      {workflows.data && workflows.data.items.length > 0 && view.total === 0 && (
        <div className="empty-state">No workflow matches{q.trim() ? ` “${q.trim()}”` : ""}{changed !== "any" ? ` changed ${CHANGED.find((c) => c.id === changed)!.label.toLowerCase()}` : ""}.</div>
      )}
      {act.error && <p className="error-text small" role="alert">{(act.error as Error).message}</p>}
      {view.total > 0 && <table className="table wf-table">
        <thead><tr><th>Name</th><th className="wf-version">Version</th><th>Last changed</th><th><span className="visually-hidden">Actions</span></th></tr></thead>
        <tbody data-testid="workflow-list">
        {view.rows.map((w) => (
          <tr key={w.id}>
            <td className="wf-name"><Link to={`/workflows/${w.id}`}>{w.name}</Link></td>
            <td className="wf-version muted small">v{w.version}</td>
            <td className="muted small" title={exact(w.updated_at)}>{ago(w.updated_at)}</td>
            <td className="wf-actions">{canBuild && (
              <Menu testId={`workflow-menu-${w.name}`} title={`More for ${w.name}`} className="row-menu"
                label={<MoreHorizontal size={16} aria-label={`more for ${w.name}`} />}>
                {(close) => (
                  <>
                    <button type="button" role="menuitem" onClick={async () => {
                      close();
                      const name = (await promptDialog({ title: "Rename the workflow", confirm: "Rename", input: { label: "Name", value: w.name, required: true } }))?.trim();
                      if (name && name !== w.name) act.mutate({ kind: "rename", id: w.id, name });
                    }}>Rename…</button>
                    <button type="button" role="menuitem" onClick={() => { close(); act.mutate({ kind: "duplicate", id: w.id }); }}>Duplicate</button>
                    <hr />
                    <button type="button" role="menuitem" className="danger-item" onClick={async () => {
                      close();
                      if (await confirmDialog({
                        title: `Delete ${w.name}?`,
                        body: "Its versions, runs, published endpoints, datasets and evaluations are deleted too. This cannot be undone.",
                        confirm: "Delete", danger: true,
                      })) act.mutate({ kind: "delete", id: w.id, name: w.name });
                    }}>Delete…</button>
                  </>
                )}
              </Menu>
            )}</td>
          </tr>
        ))}
        </tbody>
      </table>}
      {view.pages > 1 && (
        <nav className="pager" aria-label="workflow pages">
          <span className="muted small">{view.from}–{view.to} of {view.total}</span>
          <span className="spacer" />
          <button type="button" className="small" aria-label="previous page" disabled={view.page === 1}
            onClick={() => setPage(view.page - 1)}><ChevronLeft size={14} /></button>
          {pageNumbers(view.page, view.pages).map((n, i) => n === "…"
            ? <span key={`gap${i}`} className="muted small">…</span>
            : <button type="button" key={n} className={`small${n === view.page ? " active" : ""}`} aria-current={n === view.page ? "page" : undefined}
                aria-label={`page ${n}`} onClick={() => setPage(n)}>{n}</button>)}
          <button type="button" className="small" aria-label="next page" disabled={view.page === view.pages}
            onClick={() => setPage(view.page + 1)}><ChevronRight size={14} /></button>
        </nav>
      )}
    </section>
  );
}
