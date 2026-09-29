/** Home: workflows of the default project; create empty or from a template. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { PROJECT_ID, useTemplates, useWorkflows } from "../api/queries";

export function HomePage() {
  const workflows = useWorkflows();
  const templates = useTemplates();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: (body: { name: string; template?: string }) =>
      apiFetch<{ id: string }>(`/projects/${PROJECT_ID}/workflows`, { method: "POST", json: body }),
    onSuccess: (wf) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${wf.id}`);
    },
  });

  return (
    <div className="page home">
      <header className="topbar">
        <span className="brand">AgentCanvas</span><span className="crumb">/ Default project</span><span className="spacer" />
        <Link to="/inbox">Inbox</Link>
      </header>
      <section>
        <h2>New workflow</h2>
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); create.mutate({ name: name.trim() || "Untitled workflow" }); }}>
          <input placeholder="Workflow name" value={name} onChange={(e) => setName(e.target.value)} aria-label="workflow name" />
          <button type="submit" className="primary">Create empty</button>
        </form>
        <h3>…or start from a template</h3>
        <div className="cards">
          {(templates.data ?? []).map((t) => (
            <button type="button" key={t.id} className="card" data-testid={`template-${t.id}`}
              onClick={() => create.mutate({ name: name.trim() || t.name, template: t.id })}>
              <b>{t.name}</b>
              <span className="muted">{t.description}</span>
              <small>{t.node_count} nodes</small>
            </button>
          ))}
        </div>
        {create.error && <p className="error-text">{(create.error as Error).message}</p>}
      </section>
      <section>
        <h2>Workflows</h2>
        {workflows.isLoading && <p className="muted">Loading…</p>}
        {workflows.data?.items.length === 0 && <p className="muted">No workflows yet.</p>}
        <ul className="wf-list">
          {workflows.data?.items.map((w) => (
            <li key={w.id}>
              <Link to={`/workflows/${w.id}`}>{w.name}</Link>{" "}
              <small className="muted">v{w.version} · {new Date(w.updated_at).toLocaleString()}</small>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
