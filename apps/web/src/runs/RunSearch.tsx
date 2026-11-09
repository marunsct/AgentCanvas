/** "Find conversations where …" (G-10): search the project's runs by meaning, with the project's embedding model. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { RunSearchResult } from "../api/generated";
import { PROJECT_ID } from "../api/queries";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";

export function RunSearch({ workflowId }: { workflowId?: string }) {
  const [draft, setDraft] = useState("");
  const [asked, setAsked] = useState("");
  const canBuild = useCan("builder");
  const qc = useQueryClient();
  const found = useQuery({
    queryKey: ["run-search", PROJECT_ID, workflowId ?? "", asked],
    enabled: asked !== "",
    queryFn: () => {
      const q = new URLSearchParams({ q: asked });
      if (workflowId) q.set("workflow_id", workflowId);
      return apiFetch<RunSearchResult>(`/projects/${PROJECT_ID}/runs/search?${q}`);
    },
  });
  const index = useMutation({
    mutationFn: () => apiFetch<{ indexed: number }>(`/projects/${PROJECT_ID}/runs/search/index`, { method: "POST" }),
    onSuccess: (r) => {
      toast(`Indexed ${r.indexed} older run${r.indexed === 1 ? "" : "s"}`);
      void qc.invalidateQueries({ queryKey: ["run-search"] });
    },
  });
  const hits = Array.isArray(found.data?.hits) ? found.data.hits : [];
  return (
    <section className="run-search" aria-label="search conversations">
      <form className="row gap" onSubmit={(e) => { e.preventDefault(); setAsked(draft.trim()); }}>
        <input type="search" value={draft} onChange={(e) => setDraft(e.target.value)} style={{ flex: 1, minWidth: 260 }}
          aria-label="find conversations where" placeholder="Find conversations where… (e.g. customers asking for a refund)" />
        <button type="submit" disabled={!draft.trim()}>Search</button>
        {asked && <button type="button" className="link" onClick={() => { setAsked(""); setDraft(""); }}>Clear</button>}
      </form>
      {asked && <QueryStatus q={found} what="the search" />}
      {found.data && (
        <div data-testid="run-search-results">
          <p className="muted small">
            {hits.length === 0 ? "No conversation matches." : `${hits.length} conversation${hits.length === 1 ? "" : "s"}, best first.`}
            {" "}Ranked with {found.data.model === "builtin" ? "the built-in offline embedding" : found.data.model}.
            {found.data.stale > 0 && (
              <> {found.data.stale} older run{found.data.stale === 1 ? " is" : "s are"} not searchable yet.
                {canBuild && <> <button type="button" className="link" disabled={index.isPending} onClick={() => index.mutate()}>Index them</button></>}</>
            )}
          </p>
          <ul className="run-hits">
            {hits.map((h) => (
              <li key={h.thread_id}>
                <div className="row gap">
                  <Link to={`/workflows/${h.workflow_id}?thread=${h.thread_id}`} title="Open in the editor's timeline">{h.workflow_name ?? h.workflow_id}</Link>
                  <span className="badge small">{Math.round(h.score * 100)}%</span>
                  {h.started_at ? <small className="muted" title={exact(h.started_at)}>{ago(h.started_at)}</small> : null}
                </div>
                <pre className="small run-snippet">{h.snippet}</pre>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
