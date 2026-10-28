/** Memory inspector (M4-T12, FR-MEM-04): browse namespaces, search and delete memories, erase a namespace. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import { QueryStatus } from "../ui/QueryStatus";
import type { NamespaceOut as Namespace, MemoryItem } from "../api/generated";
import { confirmDialog } from "../ui/dialog";
import { PageHeader } from "../ui/PageHeader";
import { useCan } from "../api/session";
import { ago, exact } from "../ui/time";
import { EmptyState } from "../ui/EmptyState";
import { Brain } from "lucide-react";



const qs = (params: Record<string, string>) => new URLSearchParams(params).toString();

export function MemoryPage() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const [open, setOpen] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const spaces = useQuery({ queryKey: ["memory", "namespaces"], queryFn: () => apiFetch<Namespace[]>("/memory/namespaces") });
  const current = selected ?? spaces.data?.[0]?.namespace ?? null;
  const items = useQuery({
    queryKey: ["memory", "items", current, search],
    queryFn: () => apiFetch<MemoryItem[]>(`/memory/items?${qs({ namespace: current!, ...(search ? { q: search } : {}) })}`),
    enabled: Boolean(current),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["memory"] });
  const remove = useMutation({
    mutationFn: (key: string) => apiFetch(`/memory/items?${qs({ namespace: current!, key })}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const erase = useMutation({
    mutationFn: (namespace: string) => apiFetch(`/memory/namespaces?${qs({ namespace })}`, { method: "DELETE" }),
    onSuccess: () => { setSelected(null); refresh(); },
  });

  return (
    <div className="page">
      <PageHeader title="Memory" description={<>What agents and memory nodes remember across conversations, per space and owner.</>} />
      <QueryStatus q={spaces} what="memory spaces" />
      {spaces.data?.length === 0 && (
        <EmptyState icon={Brain} title="Nothing has been remembered yet" testId="memory-empty">
          Agents with long-term memory, and Remember blocks, store what they learn here, per space and person.
        </EmptyState>
      )}
      <div className="memory-layout">
        <nav aria-label="memory namespaces">
          {spaces.data?.map((s) => (
            <button type="button" key={s.namespace} className={s.namespace === current ? "active" : ""}
              onClick={() => setSelected(s.namespace)}>{s.namespace}</button>
          ))}
        </nav>
        {current && (
          <section aria-label="memories">
            <div className="row gap">
              <input placeholder="Search memories…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="search memories" />
              {canBuild && <button type="button" className="danger" disabled={erase.isPending}
                onClick={async () => { if (await confirmDialog({ title: `Erase every memory under ${current}?`, body: "This cannot be undone.", confirm: "Erase all", danger: true })) erase.mutate(current); }}>
                Erase all
              </button>}
            </div>
            <QueryStatus q={items} what="memories" />
            {(remove.error ?? erase.error) && <p className="error-text small" role="alert">Could not delete: {((remove.error ?? erase.error) as Error).message}</p>}
            <table className="memory-table">
              <thead><tr><th>Key</th><th>Value</th><th>Updated</th><th /></tr></thead>
              <tbody>
                {items.data?.map((i) => (
                  <tr key={i.key} data-testid="memory-item">
                    <td><code>{i.key}</code></td>
                    <td>{(() => {
                      const text = JSON.stringify(i.value, null, 2);
                      const long = text.length > 600;
                      return <>
                        <pre className="json">{long && open !== i.key ? `${text.slice(0, 600)}…` : text}</pre>
                        {long && <button type="button" className="link small" onClick={() => setOpen(open === i.key ? null : i.key)}>{open === i.key ? "Show less" : "Show all"}</button>}
                      </>;
                    })()}</td>
                    <td className="muted small" title={i.updated_at ? exact(i.updated_at) : undefined}>{i.updated_at ? ago(i.updated_at) : ""}</td>
                    <td>{canBuild && <button type="button" aria-label={`delete ${i.key}`} onClick={async () => {
                      if (await confirmDialog({ title: `Delete the memory ${i.key}?`, body: "Agents will no longer recall it.", confirm: "Delete", danger: true })) remove.mutate(i.key);
                    }}>Delete</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items.data?.length === 0 && <p className="muted">No memories match.</p>}
          </section>
        )}
      </div>
    </div>
  );
}
