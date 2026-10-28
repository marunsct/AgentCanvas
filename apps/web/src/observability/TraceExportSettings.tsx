/** Trace export (S15): send every run of this project to Langfuse, Phoenix, Datadog or any OTLP endpoint. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { can } from "../api/session";

type Kind = "langfuse" | "phoenix" | "datadog" | "otlp";
type Ref = { $secret: string };
type Config = {
  enabled?: boolean; kind: Kind; endpoint?: string; public_key?: Ref | null; secret_key?: Ref | null;
  api_key?: Ref | null; headers?: Record<string, string | Ref>; include_content?: boolean;
};
type Out = { config: Config | null; guides: Record<Kind, string>; defaults: Record<string, string>; missing_secrets: string[] };

const LABELS: Record<Kind, string> = { langfuse: "Langfuse", phoenix: "Arize Phoenix", datadog: "Datadog (through the Agent)", otlp: "Other (OTLP over HTTP)" };
const ref = (name: string): Ref | null => (name.trim() ? { $secret: name.trim() } : null);

export function TraceExportSettings() {
  const qc = useQueryClient();
  const key = ["trace-export", PROJECT_ID];
  const q = useQuery({ queryKey: key, queryFn: () => apiFetch<Out>(`/projects/${PROJECT_ID}/trace-export`) });
  const [kind, setKind] = useState<Kind | "">("");
  const [endpoint, setEndpoint] = useState("");
  const [pk, setPk] = useState("");
  const [sk, setSk] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [authSecret, setAuthSecret] = useState("");
  const [content, setContent] = useState(true);
  useEffect(() => {
    const c = q.data?.config;
    if (!c) return;
    setKind(c.kind); setEndpoint(c.endpoint ?? ""); setPk(c.public_key?.$secret ?? ""); setSk(c.secret_key?.$secret ?? "");
    setApiKey(c.api_key?.$secret ?? ""); setContent(c.include_content ?? true);
    const auth = c.headers?.Authorization;
    setAuthSecret(typeof auth === "object" ? auth.$secret : "");
  }, [q.data]);
  const done = (out: Out) => qc.setQueryData(key, out);
  const save = useMutation({
    mutationFn: () => apiFetch<Out>(`/projects/${PROJECT_ID}/trace-export`, {
      method: "PUT",
      json: {
        kind, endpoint: endpoint.trim(), include_content: content,
        public_key: kind === "langfuse" ? ref(pk) : null, secret_key: kind === "langfuse" ? ref(sk) : null,
        api_key: kind === "phoenix" ? ref(apiKey) : null,
        headers: kind === "otlp" && authSecret.trim() ? { Authorization: { $secret: authSecret.trim() } } : {},
      },
    }),
    onSuccess: done,
  });
  const off = useMutation({ mutationFn: () => apiFetch<Out>(`/projects/${PROJECT_ID}/trace-export`, { method: "DELETE" }), onSuccess: (o) => { done(o); setKind(""); } });
  const test = useMutation({ mutationFn: () => apiFetch<{ ok: boolean; detail: string }>(`/projects/${PROJECT_ID}/trace-export/test`, { method: "POST" }) });
  if (!q.data) return null;
  const admin = can("admin");
  const savedKind = q.data.config?.kind;
  const pickKind = (k: Kind | "") => { setKind(k); save.reset(); test.reset(); };

  return (
    <section className="settings-card setup-form" aria-label="Trace export" data-testid="trace-export">
      <h2>Observability</h2>
      <p className="muted small">Send traces to your observability tool.</p>
      <p className="small muted">
        Every run of this project's workflows is sent as an OpenTelemetry trace: the run, each step, every model call
        (model, tokens, reply) and every tool call. Secrets are masked, and fields a workflow lists under "never in
        traces" are redacted.
      </p>
      <label htmlFor="trace-kind">Destination</label>
      <select id="trace-kind" value={kind} disabled={!admin} onChange={(e) => pickKind(e.target.value as Kind | "")}>
        <option value="">Off</option>
        {(Object.keys(LABELS) as Kind[]).map((k) => <option key={k} value={k}>{LABELS[k]}</option>)}
      </select>
      {kind && (
        <>
          <p className="small muted">{q.data.guides[kind]}</p>
          <label htmlFor="trace-endpoint">{kind === "otlp" ? "Traces URL" : "Address"}</label>
          <input id="trace-endpoint" className="mono" disabled={!admin} value={endpoint} onChange={(e) => setEndpoint(e.target.value)}
            placeholder={kind === "otlp" ? "https://collector.example.com/v1/traces" : q.data.defaults[kind]} />
          {kind === "langfuse" && (
            <>
              <label htmlFor="trace-pk">Secret holding the public key</label>
              <input id="trace-pk" className="mono" disabled={!admin} value={pk} onChange={(e) => setPk(e.target.value)} placeholder="LANGFUSE_PUBLIC_KEY" />
              <label htmlFor="trace-sk">Secret holding the secret key</label>
              <input id="trace-sk" className="mono" disabled={!admin} value={sk} onChange={(e) => setSk(e.target.value)} placeholder="LANGFUSE_SECRET_KEY" />
            </>
          )}
          {kind === "phoenix" && (
            <>
              <label htmlFor="trace-apikey">Secret holding the API key (optional)</label>
              <input id="trace-apikey" className="mono" disabled={!admin} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="PHOENIX_API_KEY" />
            </>
          )}
          {kind === "otlp" && (
            <>
              <label htmlFor="trace-auth">Secret holding the Authorization header (optional)</label>
              <input id="trace-auth" className="mono" disabled={!admin} value={authSecret} onChange={(e) => setAuthSecret(e.target.value)} placeholder="OTLP_AUTH" />
            </>
          )}
          <label><input type="checkbox" disabled={!admin} checked={content} onChange={(e) => setContent(e.target.checked)} />
            {" "}Include inputs, outputs and model replies</label>
        </>
      )}
      {kind === savedKind && q.data.missing_secrets.length > 0 && (
        <p className="error-text small">Add these under Secrets: {q.data.missing_secrets.join(", ")}</p>
      )}
      {admin ? (
        <div className="row gap">
          {kind && <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save</button>}
          {!kind && q.data.config && <button type="button" disabled={off.isPending} onClick={() => off.mutate()}>Turn off</button>}
          {kind && kind === savedKind && <button type="button" disabled={test.isPending} onClick={() => test.mutate()}>Send a test trace</button>}
          {save.isSuccess && <span className="ok-text small">Saved</span>}
        </div>
      ) : <p className="small muted">Only an admin can change where traces go.</p>}
      {test.data && <p className={`small ${test.data.ok ? "ok-text" : "error-text"}`} role="status">{test.data.detail}</p>}
      {(save.error || test.error) && <p className="error-text small">{((save.error || test.error) as Error).message}</p>}
    </section>
  );
}
