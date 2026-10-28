/** Memory inspector (M4-T12, FR-MEM-04): browse namespaces, search and delete memories, erase a namespace. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { QueryStatus } from "../ui/QueryStatus";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import type { NamespaceOut as Namespace, MemoryItem } from "../api/generated";
import { confirmDialog } from "../ui/dialog";



const qs = (params: Record<string, string>) => new URLSearchParams(params).toString();

export function MemoryPage() {
  const qc = useQueryClient();
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
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Memory</span><span className="spacer" /><ThemeSwitcher /></header>
      <h2>Long-term memory</h2>
      <p className="muted">What agents and memory nodes remember across conversations, per space and owner.</p>
      <QueryStatus q={spaces} what="memory spaces" />
      {spaces.data?.length === 0 && <p className="muted" data-testid="memory-empty">Nothing has been remembered yet.</p>}
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
              <button type="button" className="danger" disabled={erase.isPending}
                onClick={async () => { if (await confirmDialog({ title: `Erase every memory under ${current}?`, body: "This cannot be undone.", confirm: "Erase all", danger: true })) erase.mutate(current); }}>
                Erase all
              </button>
            </div>
            <QueryStatus q={items} what="memories" />
            <table className="memory-table">
              <thead><tr><th>Key</th><th>Value</th><th>Updated</th><th /></tr></thead>
              <tbody>
                {items.data?.map((i) => (
                  <tr key={i.key} data-testid="memory-item">
                    <td><code>{i.key}</code></td>
                    <td><pre className="json">{JSON.stringify(i.value, null, 2).slice(0, 600)}</pre></td>
                    <td className="muted small">{i.updated_at?.replace("T", " ").slice(0, 19)}</td>
                    <td><button type="button" aria-label={`delete ${i.key}`} onClick={() => remove.mutate(i.key)}>Delete</button></td>
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
