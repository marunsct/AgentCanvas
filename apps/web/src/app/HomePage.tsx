/** Home: workflows of the current project; builders create empty or from a template. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { maturaMode, signInEnabled, signOut, switchOrganisation } from "../api/auth";
import { chooseWorkspace, PROJECT_ID, PROJECT_NAME, useSetup, useTemplates, useWorkflows, WORKSPACE_ID, WORKSPACES } from "../api/queries";
import { can, ME } from "../api/session";
import { FileImport, FORMAT_LABELS, type ImportOut } from "../release/Import";
import { startTour } from "../tutor/Tutor";
import { QueryStatus } from "../ui/QueryStatus";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { TemplateGallery } from "./TemplateGallery";

export function HomePage() {
  const workflows = useWorkflows();
  const templates = useTemplates();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [imported, setImported] = useState<ImportOut | null>(null);
  // Matura workspaces (TS26): picking one moves the page to one of its projects
  const [, setWorkspace] = useState(WORKSPACE_ID);
  const pickWorkspace = useMutation({
    mutationFn: (id: string | null) => chooseWorkspace(id),
    onSettled: () => { setWorkspace(WORKSPACE_ID); void qc.invalidateQueries(); },
  });
  const onImported = (out: ImportOut) => {
    void qc.invalidateQueries({ queryKey: ["workflows"] });
    // a file AgentCanvas wrote comes back exactly: open it; otherwise show what was approximated first
    if (out.report.every((r) => r.startsWith("Restored exactly"))) navigate(`/workflows/${out.id}`);
    else setImported(out);
  };
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
        <span className="brand">AgentCanvas</span><span className="crumb" data-testid="project-name">/ {PROJECT_NAME}</span>
        {WORKSPACES.length > 0 && (
          <select aria-label="workspace" data-testid="workspace-switcher" value={WORKSPACE_ID ?? ""} disabled={pickWorkspace.isPending}
            onChange={(e) => pickWorkspace.mutate(e.target.value || null)}>
            <option value="">All workspaces</option>
            {WORKSPACES.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        <span className="spacer" />
        {can("builder") && <Link to="/setup" data-testid="setup-link">Model</Link>}
        {can("builder") && <Link to="/playground" data-testid="playground-link">Playground</Link>}
        <Link to="/inbox">Inbox</Link>
        <Link to="/knowledge" data-testid="knowledge-link">Knowledge</Link>
        <Link to="/memory" data-testid="memory-link">Memory</Link>
        <Link to="/workspace" data-testid="workspace-link">Workspace</Link>
        <Link to="/events" data-testid="events-link">Events</Link>
        {can("admin") && <Link to="/members" data-testid="members-link">Members</Link>}
        {can("admin") && <Link to="/audit" data-testid="audit-link">Audit log</Link>}
        <Link to="/policies" data-testid="policies-link">Policies</Link>
        {maturaMode() && Object.keys(ME.memberships).length > 1 && (
          <select aria-label="organisation" data-testid="org-switcher" value={ME.org_id}
            onChange={(e) => void switchOrganisation(e.target.value)}>
            {Object.keys(ME.memberships).map((id) => <option key={id} value={id}>{ME.org_names?.[id] ?? id}</option>)}
          </select>
        )}
        <span className="me-chip" data-testid="me" title={ME.email}>{ME.email || "you"} · {ME.role}</span>
        {signInEnabled() && <button type="button" className="link small" onClick={() => void signOut()}>Sign out</button>}
        <ThemeSwitcher />
      </header>
      <Invitations />
      {pickWorkspace.error && <p className="error-text" role="alert">{(pickWorkspace.error as Error).message}</p>}
      {!can("builder") && (
        <p className="muted" data-testid="read-only-note">You can view workflows and runs here; your role ({ME.role}) cannot create or edit them.</p>
      )}
      {can("builder") && <>
      <SetupBanner />
      <section className="guided">
        <h2>New here?</h2>
        <p className="muted">Build a real workflow in six short steps — state, steps, flow, a model, a run and a human approval.</p>
        <div className="row gap">
          <button type="button" className="primary" data-testid="start-tour" onClick={() => tour.mutate()}>Start the guided build</button>
          <Link to="/playground" className="button" data-testid="try-playground">Try a model in the playground</Link>
        </div>
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
        {templates.data && (
          <TemplateGallery templates={templates.data} busy={create.isPending}
            onPick={(t) => create.mutate({ name: name.trim() || t.name, template: t.id })} />
        )}
        {create.error && <p className="error-text">{(create.error as Error).message}</p>}
        <h3>…or bring one from Langflow, Flowise, n8n or another agent framework</h3>
        <FileImport projectId={PROJECT_ID} onDone={onImported} />
        {imported && (
          <div className="notes small" data-testid="import-notes">
            <p>Imported <b>{imported.name}</b>{imported.format ? ` from ${FORMAT_LABELS[imported.format] ?? imported.format}` : ""}.
              {imported.report.length ? " Check these before running it:" : " Everything carried over."}</p>
            <ul>{imported.report.map((r) => <li key={r}>{r}</li>)}</ul>
            <Link to={`/workflows/${imported.id}`} className="button primary">Open {imported.name}</Link>
          </div>
        )}
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

/** Until the project has a default model: a prompt to choose one (any provider, or a local model). */
function SetupBanner() {
  const setup = useSetup();
  if (!setup.data) return null;
  const dm = setup.data.default_model;
  if (setup.data.done) {
    return dm ? <p className="muted small" data-testid="default-model">New workflows use <code>{dm.model}</code>. <Link to="/setup">Change</Link></p> : null;
  }
  return (
    <section className="guided" data-testid="setup-banner">
      <h2>Set up your model</h2>
      <p className="muted">Choose the AI model your workflows use: Claude, GPT, Gemini, Mistral, Groq, DeepSeek, Grok, or a local model with Ollama, LM Studio, vLLM or llama.cpp.</p>
      <Link to="/setup" className="button primary">Choose a model</Link>
    </section>
  );
}

interface Invitation { org_id: string; org_name: string; role: string }

/** Organisations that invited you while you belong to another one: joining is your choice (S11). */
function Invitations() {
  const invitations = useQuery({ queryKey: ["invitations"], queryFn: () => apiFetch<Invitation[]>("/me/invitations") });
  const answer = useMutation({
    mutationFn: ({ org, accept }: { org: string; accept: boolean }) =>
      apiFetch(`/me/invitations/${encodeURIComponent(org)}${accept ? "/accept" : ""}`, { method: accept ? "POST" : "DELETE" }),
    // accepting switches to the organisation just joined: start over in it
    onSuccess: (_, { accept }) => (accept ? location.assign("/") : void invitations.refetch()),
  });
  if (!invitations.data?.length) return null;
  return (
    <section className="guided" data-testid="invitations">
      {invitations.data.map((i) => (
        <p key={i.org_id} className="row gap">
          <span>You are invited to join <b>{i.org_name}</b> as {i.role}.</span>
          <button type="button" className="primary" onClick={() => answer.mutate({ org: i.org_id, accept: true })}>Join</button>
          <button type="button" onClick={() => answer.mutate({ org: i.org_id, accept: false })}>Decline</button>
        </p>
      ))}
      {answer.error && <p className="error-text">{(answer.error as Error).message}</p>}
    </section>
  );
}
