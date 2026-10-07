/** Copilot settings (S14): on/off, the same model as the workflow or a separate one, and a token limit per chat. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type * as G from "../api/generated";
import { PROJECT_ID, useModels } from "../api/queries";
import { can } from "../api/session";

type Settings = G.CopilotSettings & { resolved?: { model?: string | null; origin?: string | null } | null };

export function CopilotSettings() {
  const qc = useQueryClient();
  const models = useModels();
  const q = useQuery({
    queryKey: ["copilot-settings", PROJECT_ID],
    queryFn: () => apiFetch<Settings>(`/projects/${PROJECT_ID}/copilot-settings`),
  });
  const [enabled, setEnabled] = useState(true);
  const [source, setSource] = useState<"workflow" | "separate">("workflow");
  const [model, setModel] = useState("");
  const [cap, setCap] = useState(400_000);
  useEffect(() => {
    if (!q.data) return;
    setEnabled(q.data.enabled ?? true);
    setSource((q.data.source as "workflow" | "separate") ?? "workflow");
    setModel(q.data.model ?? "");
    setCap(q.data.token_cap ?? 400_000);
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<Settings>(`/projects/${PROJECT_ID}/copilot-settings`, {
      method: "PUT",
      json: { enabled, source, model: source === "separate" ? model.trim() || null : null, token_cap: cap },
    }),
    onSuccess: (out) => qc.setQueryData(["copilot-settings", PROJECT_ID], out),
  });
  if (!q.data) return null;

  return (
    <section className="setup-form" aria-label="Copilot settings" data-testid="copilot-settings">
      <h3>AI Copilot</h3>
      <p className="small muted">
        The Copilot chats with you in the editor and proposes changes you accept or reject. It can use the same model as
        your workflows, or a separate one (for example a stronger model for building, or a local one for privacy).
      </p>
      <label><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Turn on the Copilot</label>
      <label><input type="radio" name="copilot-source" checked={source === "workflow"} onChange={() => setSource("workflow")} />
        {" "}Use the workflow's model{q.data.resolved?.origin === "project default" ? ` (${q.data.resolved.model})` : ""}</label>
      <label><input type="radio" name="copilot-source" checked={source === "separate"} onChange={() => setSource("separate")} />
        {" "}Use a separate model</label>
      {source === "separate" && (
        <input className="mono" list="copilot-models" aria-label="Copilot model" placeholder="provider:model, e.g. anthropic:claude-sonnet-5-5"
          value={model} onChange={(e) => setModel(e.target.value)} />
      )}
      <datalist id="copilot-models">{(models.data ?? []).filter((m) => m.tools !== false).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</datalist>
      <label htmlFor="copilot-cap">Token limit per chat</label>
      <input id="copilot-cap" type="number" min={1000} step={1000} value={cap} onChange={(e) => setCap(Number(e.target.value))} />
      <div className="row gap">
        <button type="button" className="primary" disabled={!can("builder") || save.isPending || (source === "separate" && !model.trim())}
          onClick={() => save.mutate()}>Save Copilot settings</button>
        {save.isSuccess && <span className="ok-text small">Saved</span>}
      </div>
      {save.error && <p className="error-text small">{(save.error as Error).message}</p>}
    </section>
  );
}
