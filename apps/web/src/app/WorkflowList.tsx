/** Home's list of the project's workflows: search, sort, and per workflow open, rename, duplicate or delete. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LayoutGrid, MoreHorizontal } from "lucide-react";
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

type Sort = "updated" | "name";

export function WorkflowList() {
  const workflows = useWorkflows();
  const canBuild = useCan("builder");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("updated");
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
  const items = useMemo(() => {
    const all = workflows.data?.items ?? [];
    const needle = q.trim().toLowerCase();
    const found = needle ? all.filter((w) => w.name.toLowerCase().includes(needle)) : all;
    return [...found].sort((a, b) => (sort === "name" ? a.name.localeCompare(b.name)
      : new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()));
  }, [workflows.data, q, sort]);

  return (
    <section aria-label="Workflows">
      <div className="row gap list-tools">
        <input type="search" placeholder="Search workflows" aria-label="search workflows" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="sort workflows" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
          <option value="updated">Last changed</option>
          <option value="name">Name</option>
        </select>
      </div>
      <QueryStatus q={workflows} what="workflows" />
      {workflows.data?.items.length === 0 && (
        <EmptyState icon={LayoutGrid} title="No workflows yet">Start from a template below, or with an empty canvas.</EmptyState>
      )}
      {workflows.data && workflows.data.items.length > 0 && items.length === 0 && (
        <div className="empty-state">No workflow matches “{q}”.</div>
      )}
      {act.error && <p className="error-text small" role="alert">{(act.error as Error).message}</p>}
      <ul className="wf-list" data-testid="workflow-list">
        {items.map((w) => (
          <li key={w.id}>
            <Link to={`/workflows/${w.id}`}>{w.name}</Link>
            <small className="muted" title={exact(w.updated_at)}>v{w.version} · changed {ago(w.updated_at)}</small>
            {canBuild && (
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
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
