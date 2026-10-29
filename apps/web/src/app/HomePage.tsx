/** Home: workflows of the current project; builders create empty or from a template. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { PROJECT_ID, PROJECT_NAME, useSetup, useTemplates, useWorkflows } from "../api/queries";
import { useCan, useMe } from "../api/session";
import { FileImport, FORMAT_LABELS, type ImportOut } from "../release/Import";
import { startTour } from "../tutor/Tutor";
import { QueryStatus } from "../ui/QueryStatus";
import { PageHeader } from "../ui/PageHeader";
import { kitNote } from "./gallery";
import { TemplateGallery } from "./TemplateGallery";
import { WorkflowList } from "./WorkflowList";
import type { InvitationOut as Invitation, KitOut } from "../api/generated";
import { toast } from "../ui/dialog";

export function HomePage() {
  const me = useMe((s) => s.me);
  const canBuild = useCan("builder");
  const workflows = useWorkflows();
  const templates = useTemplates();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [imported, setImported] = useState<ImportOut | null>(null);
  const onImported = (out: ImportOut) => {
    void qc.invalidateQueries({ queryKey: ["workflows"] });
    // a file AgentCanvas wrote comes back exactly: open it; otherwise show what was approximated first
    if (out.report.every((r) => r.startsWith("Restored exactly"))) navigate(`/workflows/${out.id}`);
    else setImported(out);
  };
  const create = useMutation({
    mutationFn: (body: { name: string; template?: string }) =>
      apiFetch<{ id: string; kit?: KitOut | null }>(`/projects/${PROJECT_ID}/workflows`, { method: "POST", json: body }),
    onSuccess: (wf) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      const note = kitNote(wf.kit);
      if (note) toast(note);
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
      <PageHeader title="Workflows" description={`Everything built in ${PROJECT_NAME}.`}
        actions={canBuild ? <>
          <a className="button" href="#new-workflow">+ New workflow</a>
          <button type="button" data-testid="start-tour" disabled={tour.isPending} onClick={() => tour.mutate()}
            title="Build a real workflow in six short steps">Guided build</button>
        </> : undefined} />
      <Invitations />
      {!canBuild && (
        <p className="muted" data-testid="read-only-note">You can view workflows and runs here; your role ({me.role}) cannot create or edit them.</p>
      )}
      {canBuild && <>
      <SetupBanner />
      {(workflows.data?.items.length ?? 0) < 3 && workflows.data && (
        <section className="guided">
          <h2>New here?</h2>
          <p className="muted">Build a real workflow in six short steps — state, steps, flow, a model, a run and a human approval.</p>
          <div className="row gap">
            <button type="button" className="primary" disabled={tour.isPending} onClick={() => tour.mutate()}>Start the guided build</button>
            <Link to="/playground" className="button" data-testid="try-playground">Try a model in the playground</Link>
          </div>
        </section>
      )}
      {tour.error && <p className="error-text">{(tour.error as Error).message}</p>}
      </>}
      <WorkflowList />
      {canBuild && <>
      <section id="new-workflow">
        <h2>New workflow</h2>
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); create.mutate({ name: name.trim() || "Untitled workflow" }); }}>
          <input placeholder="Workflow name" value={name} onChange={(e) => setName(e.target.value)} aria-label="workflow name" />
          <button type="submit" className="primary" disabled={create.isPending}>Create empty</button>
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
    </div>
  );
}

/** Until the project has a default model: a prompt to choose one (any provider, or a local model). */
function SetupBanner() {
  const setup = useSetup();
  if (!setup.data) return null;
  const dm = setup.data.default_model;
  if (setup.data.done) {
    return dm ? <p className="muted small" data-testid="default-model">New workflows use <code>{dm.model}</code>. <Link to="/settings/models">Change</Link></p> : null;
  }
  return (
    <section className="guided" data-testid="setup-banner">
      <h2>Set up your model</h2>
      <p className="muted">Choose the AI model your workflows use: Claude, GPT, Gemini, Mistral, Groq, DeepSeek, Grok, or a local model with Ollama, LM Studio, vLLM or llama.cpp.</p>
      <Link to="/settings/models" className="button primary">Choose a model</Link>
    </section>
  );
}


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
