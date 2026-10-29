/** Settings › Project › Knowledge: how new knowledge bases split their documents unless they say otherwise. Existing
 * knowledge bases keep their own (the Knowledge page changes one). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { KnowledgeDefaults } from "../api/generated";
import { PROJECT_ID } from "../api/queries";
import { useCan } from "../api/session";
import { STRATEGIES } from "../knowledge/knowledge";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

type Chunking = Required<NonNullable<KnowledgeDefaults["chunking"]>>;

export function KnowledgeSettings() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const url = `/projects/${PROJECT_ID}/knowledge-defaults`;
  const q = useQuery({ queryKey: ["knowledge-defaults", PROJECT_ID], queryFn: () => apiFetch<KnowledgeDefaults>(url) });
  const [draft, setDraft] = useState<Chunking | null>(null);
  useEffect(() => { if (q.data) setDraft({ strategy: "markdown", size: 800, overlap: 100, ...q.data.chunking }); }, [q.data]);
  const save = useMutation({
    mutationFn: (chunking: Chunking) => apiFetch<KnowledgeDefaults>(url, { method: "PUT", json: { chunking } }),
    onSuccess: (out) => { qc.setQueryData(["knowledge-defaults", PROJECT_ID], out); toast("Saved the knowledge defaults"); },
  });
  if (!q.data || !draft) return <section className="settings-card"><h2>Knowledge</h2><QueryStatus q={q} what="the knowledge defaults" /></section>;
  const set = (patch: Partial<Chunking>) => setDraft({ ...draft, ...patch });
  const num = (v: string) => Number(v) || 0;
  const valid = draft.size >= 100 && draft.size <= 8000 && draft.overlap >= 0 && draft.overlap <= 2000;
  return (
    <section className="settings-card" aria-label="Knowledge defaults">
      <h2>Knowledge</h2>
      <p className="muted small">
        How a new knowledge base splits its documents into passages. A knowledge base can change its own on the Knowledge
        page; changing this does not touch the ones you have.
      </p>
      {!canBuild && <p className="muted small">You can read these; a builder can change them.</p>}
      <form className="stack settings-form" onSubmit={(e) => { e.preventDefault(); if (valid) save.mutate(draft); }}>
        <label>
          Split documents
          <select value={draft.strategy} disabled={!canBuild} onChange={(e) => set({ strategy: e.target.value as Chunking["strategy"] })}>
            {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </label>
        <label>
          About this many characters per passage (100 to 8000)
          <input type="number" min={100} max={8000} step={50} value={draft.size} disabled={!canBuild}
            onChange={(e) => set({ size: num(e.target.value) })} />
        </label>
        {draft.strategy === "fixed" && (
          <label>
            Characters shared between passages (0 to 2000)
            <input type="number" min={0} max={2000} step={10} value={draft.overlap} disabled={!canBuild}
              onChange={(e) => set({ overlap: num(e.target.value) })} />
          </label>
        )}
        {canBuild && (
          <div className="row gap">
            <button type="submit" className="primary" disabled={!valid || save.isPending}>Save</button>
            {!valid && <span className="error-text small" role="alert">Size 100 to 8000, overlap 0 to 2000.</span>}
          </div>
        )}
        {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
      </form>
    </section>
  );
}
