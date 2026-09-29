/** Triggers (M6-T5): signed webhooks and cron schedules, on the published version or the draft. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { API_BASE, apiFetch } from "../api/client";
import type { DeploymentOut, TriggerCreated, TriggerOut } from "../api/generated";

export function TriggersSection({ wfId }: { wfId: string }) {
  const qc = useQueryClient();
  const key = ["triggers", wfId];
  const triggers = useQuery({ queryKey: key, queryFn: () => apiFetch<TriggerOut[]>(`/workflows/${wfId}/triggers`) });
  const deps = useQuery({ queryKey: ["deployments", wfId], queryFn: () => apiFetch<DeploymentOut[]>(`/workflows/${wfId}/deployments`) });
  const published = deps.data?.find((d) => d.active);
  const [schedule, setSchedule] = useState("0 9 * * 1-5");
  const [input, setInput] = useState("{}");
  const [created, setCreated] = useState<TriggerCreated | null>(null);
  const [error, setError] = useState<string | null>(null);
  const create = async (kind: "webhook" | "cron") => {
    setError(null);
    try {
      const config = kind === "cron" ? { schedule, input: JSON.parse(input || "{}") } : {};
      const out = await apiFetch<TriggerCreated>(`/workflows/${wfId}/triggers`, {
        method: "POST", json: { kind, config, deployment_id: published?.id ?? null },
      });
      setCreated(out.kind === "webhook" ? out : null);
      void qc.invalidateQueries({ queryKey: key });
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const toggle = async (t: TriggerOut) => {
    await apiFetch(`/triggers/${t.id}`, { method: "PATCH", json: { enabled: !t.enabled } });
    void qc.invalidateQueries({ queryKey: key });
  };
  const remove = async (t: TriggerOut) => {
    await apiFetch(`/triggers/${t.id}`, { method: "DELETE" });
    void qc.invalidateQueries({ queryKey: key });
  };
  return (
    <section aria-label="triggers">
      <h4>Triggers <small className="muted">{published ? `run ${published.environment} v${published.semver}` : "run the draft"}</small></h4>
      <div className="row gap">
        <button type="button" className="small" onClick={() => void create("webhook")}>+ Webhook</button>
        <input className="mono" value={schedule} onChange={(e) => setSchedule(e.target.value)} aria-label="cron schedule" />
        <button type="button" className="small" onClick={() => void create("cron")}>+ Cron</button>
      </div>
      <label className="small">Cron input (JSON)
        <input className="mono" value={input} onChange={(e) => setInput(e.target.value)} aria-label="cron input" />
      </label>
      {error && <p className="error-text small" role="alert">{error}</p>}
      {created && (
        <div className="key-box small" data-testid="webhook-secret">
          POST <code>{location.origin}{API_BASE}{created.url?.replace(/^\/v1/, "")}</code> with header{" "}
          <code>X-AgentCanvas-Signature: sha256=HMAC(body)</code> — signing secret (shown once): <code>{created.secret}</code>
        </div>
      )}
      <ul className="version-list">
        {(triggers.data ?? []).map((t) => (
          <li key={t.id} data-testid={`trigger-${t.kind}`}>
            <div className="row gap">
              <b>{t.kind}</b> {t.kind === "cron" ? <code>{String(t.config.schedule)}</code> : <code className="small">{t.url}</code>}
              {!t.enabled && <span className="badge warn">off</span>}
              <span className="spacer" />
              <button type="button" className="small" onClick={() => void toggle(t)}>{t.enabled ? "Disable" : "Enable"}</button>
              <button type="button" className="link small" aria-label={`delete trigger ${t.id}`} onClick={() => void remove(t)}>✕</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
