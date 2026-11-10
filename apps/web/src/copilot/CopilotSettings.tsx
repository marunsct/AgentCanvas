/** Copilot settings (S14): on/off, the same model as the workflow or a separate one, and a token limit per chat. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type * as G from "../api/generated";
import { PROJECT_ID, useModels, useSecretNames } from "../api/queries";
import { can } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

type Settings = G.SettingsOut;

export function CopilotSettings() {
  const qc = useQueryClient();
  const models = useModels();
  const secrets = useSecretNames();
  const q = useQuery({
    queryKey: ["copilot-settings", PROJECT_ID],
    queryFn: () => apiFetch<Settings>(`/projects/${PROJECT_ID}/copilot-settings`),
  });
  const [enabled, setEnabled] = useState(true);
  const [source, setSource] = useState<"workflow" | "separate">("workflow");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [keySecret, setKeySecret] = useState("");
  const [cap, setCap] = useState(400_000);
  const [askModel, setAskModel] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [notes, setNotes] = useState("");
  useEffect(() => {
    if (!q.data) return;
    setEnabled(q.data.enabled ?? true);
    setSource((q.data.source as "workflow" | "separate") ?? "workflow");
    setModel(q.data.model ?? "");
    setBaseUrl(q.data.base_url ?? "");
    setKeySecret((q.data.api_key as { $secret?: string } | null | undefined)?.$secret ?? "");
    setCap(q.data.token_cap ?? 400_000);
    setAskModel(q.data.ask_model ?? "");
    setLocalOnly(q.data.privacy === "local_only");
    setNotes(q.data.notes ?? "");
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<Settings>(`/projects/${PROJECT_ID}/copilot-settings`, {
      method: "PUT",
      json: {
        enabled, source, model: source === "separate" ? model.trim() || null : null, token_cap: cap,
        base_url: source === "separate" ? baseUrl.trim() || null : null,
        api_key: source === "separate" && keySecret ? { $secret: keySecret } : null,
        ask_model: askModel.trim() || null, privacy: localOnly ? "local_only" : "standard", notes,
      },
    }),
    onSuccess: (out) => { qc.setQueryData(["copilot-settings", PROJECT_ID], out); toast("Saved the Copilot settings"); },
  });
  if (!q.data) return <section className="settings-card"><h2>Copilot</h2><QueryStatus q={q} what="the Copilot settings" /></section>;

  return (
    <section className="settings-card setup-form" aria-label="Copilot settings" data-testid="copilot-settings">
      <h2>Copilot</h2>
      <p className="small muted">
        The Copilot chats with you in the editor and proposes changes you accept or reject. It can use the same model as
        your workflows, or a separate one (for example a stronger model for building, or a local one for privacy).
      </p>
      <label><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Turn on the Copilot</label>
      <label><input type="radio" name="copilot-source" checked={source === "workflow"} onChange={() => setSource("workflow")} />
        {" "}Use the project's model{q.data.resolved?.origin === "project default" ? ` (${q.data.resolved.model})` : ""}
        <span className="muted small"> — from Settings › Models; a workflow without one uses its first AI model</span></label>
      <label><input type="radio" name="copilot-source" checked={source === "separate"} onChange={() => setSource("separate")} />
        {" "}Use a separate model</label>
      {source === "separate" && (
        <>
          <input className="mono" list="copilot-models" aria-label="Copilot model" placeholder="provider:model, e.g. anthropic:claude-sonnet-5-5"
            value={model} onChange={(e) => setModel(e.target.value)} />
          <label htmlFor="copilot-url">Server address (only for a self-hosted or OpenAI-compatible server)</label>
          <input id="copilot-url" className="mono" value={baseUrl} placeholder="https://…/v1" onChange={(e) => setBaseUrl(e.target.value)} />
          <label htmlFor="copilot-key">API key</label>
          <select id="copilot-key" value={keySecret} onChange={(e) => setKeySecret(e.target.value)}>
            <option value="">The provider's usual key (project secret or server variable)</option>
            {secrets.data?.map((n) => <option key={n.name} value={n.name}>Project secret {n.name}</option>)}
          </select>
        </>
      )}
      <datalist id="copilot-models">{(models.data ?? []).filter((m) => m.tools !== false).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</datalist>
      <label htmlFor="copilot-ask-model">Model for questions (optional, e.g. a cheaper one)</label>
      <input id="copilot-ask-model" className="mono" list="copilot-models" value={askModel}
        placeholder="same as above" onChange={(e) => setAskModel(e.target.value)} />
      <label><input type="checkbox" checked={localOnly} onChange={(e) => setLocalOnly(e.target.checked)} />
        {" "}Private: only use AI running on our own machines (Ollama, LM Studio, vLLM, llama.cpp)</label>
      <label htmlFor="copilot-notes">Project notes the Copilot always reads (conventions, preferences)</label>
      <textarea id="copilot-notes" rows={4} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)}
        placeholder="e.g. Use ollama:llama3.3 for drafts. Every workflow that emails customers needs an approval step." />
      <label htmlFor="copilot-cap">Token limit per chat</label>
      <input id="copilot-cap" type="number" min={1000} step={1000} value={cap} onChange={(e) => setCap(Number(e.target.value))} />
      <div className="row gap">
        <button type="button" className="primary" disabled={!can("builder") || save.isPending || (source === "separate" && !model.trim())}
          onClick={() => save.mutate()}>Save Copilot settings</button>
      </div>
      {save.error && <p className="error-text small">{(save.error as Error).message}</p>}
    </section>
  );
}
