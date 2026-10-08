/** First-run setup: the model new workflows use (any provider, hosted or local), its API key and a connection test. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { apiFetch } from "../api/client";
import { PROJECT_ID, useModelProviders, useModels, useSetup } from "../api/queries";
import { can } from "../api/session";
import type { SetupOut } from "../api/types";
import { QueryStatus } from "../ui/QueryStatus";
import { CopilotSettings } from "../copilot/CopilotSettings";
import { TraceExportSettings } from "../observability/TraceExportSettings";
import { providerHint } from "../inspector/widgets";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";

interface TestResult { ok: boolean; reply?: string | null; error?: string | null }

export function SetupPage() {
  const providers = useModelProviders();
  const models = useModels();
  const setup = useSetup();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [providerId, setProviderId] = useState("anthropic");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const provider = providers.data?.find((p) => p.id === providerId);
  const suggestions = useMemo(() => (models.data ?? []).filter((m) => m.provider === providerId), [models.data, providerId]);

  // start from the saved default model, if any
  useEffect(() => {
    const dm = setup.data?.default_model;
    if (!dm) return;
    const [p, ...rest] = dm.model.split(":");
    setProviderId(p);
    setModel(rest.join(":"));
    setBaseUrl(dm.base_url ?? "");
  }, [setup.data]);

  const pick = (id: string) => {
    setProviderId(id);
    setModel((models.data ?? []).find((m) => m.provider === id)?.id.split(":").slice(1).join(":") ?? "");
    setBaseUrl("");
    setApiKey("");
    test.reset();
  };

  const body = () => ({ model: `${providerId}:${model.trim()}`, base_url: baseUrl.trim() || null, api_key: apiKey.trim() || null });
  const test = useMutation({
    mutationFn: () => apiFetch<TestResult>(`/projects/${PROJECT_ID}/setup/test`, { method: "POST", json: body() }),
  });
  const done = (out: SetupOut) => { qc.setQueryData(["setup", PROJECT_ID], out); void qc.invalidateQueries({ queryKey: ["secrets"] }); navigate("/"); };
  const save = useMutation({
    mutationFn: () => apiFetch<SetupOut>(`/projects/${PROJECT_ID}/setup`, { method: "PUT", json: body() }),
    onSuccess: done,
  });
  const skip = useMutation({ mutationFn: () => apiFetch<SetupOut>(`/projects/${PROJECT_ID}/setup/skip`, { method: "POST" }), onSuccess: done });

  const needsKey = Boolean(provider?.key_env || provider?.base_url_required);
  const keyName = provider?.key_env ?? (provider?.base_url_required ? "OPENAI_COMPATIBLE_API_KEY" : null);
  const keySaved = setup.data?.key_secret === keyName && setup.data?.key_set;
  const showUrl = Boolean(provider?.local || provider?.base_url_required || provider?.id === "azure_openai");
  const hosted = (providers.data ?? []).filter((p) => !p.local);
  const local = (providers.data ?? []).filter((p) => p.local);

  return (
    <div className="page setup">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Set up your model</span><span className="spacer" /><ThemeSwitcher /></header>
      <h2>Choose the model your workflows use</h2>
      <p className="muted">
        Pick any hosted provider or a model running on your own machine. New workflows from templates will use it, and you can
        still choose another model on any Chat model box on the canvas.
      </p>
      <QueryStatus q={providers} what="model providers" />
      {!can("builder") && <p className="muted">Your role cannot change the project's model.</p>}
      {providers.data && can("builder") && (
        <form className="setup-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <label htmlFor="setup-provider">Provider</label>
          <select id="setup-provider" value={providerId} onChange={(e) => pick(e.target.value)} aria-label="provider">
            <optgroup label="Hosted">{hosted.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</optgroup>
            <optgroup label="On your machine">{local.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</optgroup>
          </select>
          {provider && <p className="small muted" data-testid="setup-hint">{providerHint(provider)}</p>}

          <label htmlFor="setup-model">Model</label>
          <input id="setup-model" className="mono" list="setup-models" value={model} required aria-label="model"
            placeholder={(provider?.example ?? "").split(":").slice(1).join(":") || "model name"} onChange={(e) => setModel(e.target.value)} />
          <datalist id="setup-models">{suggestions.map((m) => <option key={m.id} value={m.id.split(":").slice(1).join(":")}>{m.label}</option>)}</datalist>

          {showUrl && <>
            <label htmlFor="setup-url">{provider?.id === "azure_openai" ? "Endpoint (optional)" : "Server address"}</label>
            <input id="setup-url" className="mono" value={baseUrl} aria-label="base URL" required={provider?.base_url_required}
              placeholder={provider?.base_url ?? "https://…/v1"} onChange={(e) => setBaseUrl(e.target.value)} />
          </>}

          {needsKey && <>
            <label htmlFor="setup-key">API key{provider?.base_url_required ? " (if the server needs one)" : ""}</label>
            <input id="setup-key" type="password" autoComplete="off" value={apiKey} aria-label="API key"
              placeholder={keySaved ? "saved — leave empty to keep it" : `stored as the project secret ${keyName}`}
              onChange={(e) => setApiKey(e.target.value)} />
            <p className="small muted">Stored encrypted as the project secret <code>{keyName}</code>. It is never shown again.</p>
          </>}

          <div className="row gap">
            <button type="button" disabled={!model.trim() || test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
            <button type="submit" className="primary" disabled={!model.trim() || save.isPending}>Save</button>
            {!setup.data?.done && <button type="button" className="link" onClick={() => skip.mutate()}>Skip for now</button>}
          </div>
          {test.data?.ok && <p className="ok-text" data-testid="setup-test">Connected. The model replied: “{test.data.reply}”</p>}
          {test.data && !test.data.ok && <p className="error-text" data-testid="setup-test">Could not reach the model: {test.data.error}</p>}
          {(test.error || save.error || skip.error) && <p className="error-text">{((test.error || save.error || skip.error) as Error).message}</p>}
        </form>
      )}
      <CopilotSettings />
      <TraceExportSettings />
    </div>
  );
}
