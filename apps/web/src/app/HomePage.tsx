/** Home: workflows of the current project; builders create empty or from a template. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { signOut } from "../api/auth";
import { PROJECT_ID, useTemplates, useWorkflows } from "../api/queries";
import { can, ME } from "../api/session";
import { startTour } from "../tutor/Tutor";
import { QueryStatus } from "../ui/QueryStatus";

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

  const tour = useMutation({
    mutationFn: () => apiFetch<{ id: string }>(`/projects/${PROJECT_ID}/workflows`, { method: "POST", json: { name: "My first workflow" } }),
    onSuccess: (wf) => {
      startTour(wf.id);
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      navigate(`/workflows/${wf.id}`);
    },
  });

  return (
    <div className="page home">
      <header className="topbar">
        <span className="brand">AgentCanvas</span><span className="crumb">/ Default project</span><span className="spacer" />
        <Link to="/inbox">Inbox</Link>
        <Link to="/memory" data-testid="memory-link">Memory</Link>
        <Link to="/workspace" data-testid="workspace-link">Workspace</Link>
        <Link to="/events" data-testid="events-link">Events</Link>
        {can("admin") && <Link to="/members" data-testid="members-link">Members</Link>}
        {can("admin") && <Link to="/audit" data-testid="audit-link">Audit log</Link>}
        <Link to="/policies" data-testid="policies-link">Policies</Link>
        <span className="muted small" data-testid="me" title={ME.email}>{ME.email || "you"} · {ME.role}</span>
        {ME.email && <button type="button" className="link small" onClick={() => void signOut()}>Sign out</button>}
      </header>
      {!can("builder") && (
        <p className="muted" data-testid="read-only-note">You can view workflows and runs here; your role ({ME.role}) cannot create or edit them.</p>
      )}
      {can("builder") && <>
      <section className="guided">
        <h2>New here?</h2>
        <p className="muted">Build a real workflow in six short steps — state, steps, flow, a model, a run and a human approval.</p>
        <button type="button" className="primary" data-testid="start-tour" onClick={() => tour.mutate()}>Start the guided build</button>
        {tour.error && <p className="error-text">{(tour.error as Error).message}</p>}
      </section>
      <section>
        <h2>New workflow</h2>
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); create.mutate({ name: name.trim() || "Untitled workflow" }); }}>
          <input placeholder="Workflow name" value={name} onChange={(e) => setName(e.target.value)} aria-label="workflow name" />
          <button type="submit" className="primary">Create empty</button>
        </form>
        <h3>…or start from a template</h3>
        <QueryStatus q={templates} what="templates" />
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
      </>}
      <section>
        <h2>Workflows</h2>
        <QueryStatus q={workflows} what="workflows" />
        {workflows.data?.items.length === 0 && (
          <div className="empty-state">No workflows yet. Name one above and click <b>Create empty</b>, or start from a template.</div>
        )}
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
