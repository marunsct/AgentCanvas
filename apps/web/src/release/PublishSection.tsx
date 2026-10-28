/** Publish (M6-T4): pre-publish checklist, the API key (shown once), deployments with rotate / unpublish, and their
 * share links (S23). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiFetch } from "../api/client";
import type { Check, DeploymentOut, PublishOut } from "../api/generated";
import { can } from "../api/session";
import { useEditor } from "../editor/store";
import { ConnectSection } from "./ConnectSection";
import { ShareSection } from "./ShareSection";
import { confirmDialog, promptDialog } from "../ui/dialog";

export function PublishSection({ wfId }: { wfId: string }) {
  const qc = useQueryClient();
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [key, setKey] = useState<{ deployment: string; key: string } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const deps = useQuery({ queryKey: ["deployments", wfId], queryFn: () => apiFetch<DeploymentOut[]>(`/workflows/${wfId}/deployments`) });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["deployments", wfId] });
    void qc.invalidateQueries({ queryKey: ["versions", wfId] });
    void qc.invalidateQueries({ queryKey: ["test-cases", wfId] });
  };
  const publish = useMutation({
    mutationFn: async (override_reason?: string) => {
      await useEditor.getState().flush();
      return apiFetch<PublishOut>(`/workflows/${wfId}/publish`, { method: "POST", json: { environment: "prod", override_reason } });
    },
    onSuccess: (out) => {
      setChecks(out.checklist ?? []);
      if (out.api_key) setKey({ deployment: out.deployment.id, key: out.api_key });
      refresh();
    },
    onError: (e) => { setChecks(e instanceof ApiError ? (e.problem.checklist as Check[] | undefined) ?? null : null); refresh(); },
  });
  const rotate = async (d: DeploymentOut) => {
    if (!(await confirmDialog({ title: "Issue a new key?", body: "The current key stops working immediately.", confirm: "Issue a new key", danger: true }))) return;
    setActionError(null);
    try {
      const out = await apiFetch<{ api_key: string }>(`/deployments/${d.id}/rotate-key`, { method: "POST" });
      setKey({ deployment: d.id, key: out.api_key });
      refresh();
    } catch (e) {
      setActionError(`Could not rotate the key: ${(e as Error).message}`);
    }
  };
  const unpublish = async (d: DeploymentOut) => {
    if (!(await confirmDialog({ title: `Unpublish ${d.environment}?`, body: "Calls with its key will be refused.", confirm: "Unpublish", danger: true }))) return;
    setActionError(null);
    try {
      await apiFetch(`/deployments/${d.id}`, { method: "DELETE" });
      setKey((k) => (k?.deployment === d.id ? null : k));
      refresh();
    } catch (e) {
      setActionError(`Could not unpublish: ${(e as Error).message}`);
    }
  };
  return (
    <section className="publish" aria-label="publish">
      <h4>Publish</h4>
      {can("builder") && <button type="button" className="primary" disabled={publish.isPending} onClick={() => publish.mutate(undefined)}>
        {publish.isPending ? "Checking…" : "Publish to prod"}
      </button>}
      {publish.error && <p className="error-text small" role="alert">{(publish.error as Error).message}</p>}
      {actionError && <p className="error-text small" role="alert">{actionError}</p>}
      {publish.error && can("admin") && checks?.some((c) => c.name === "evaluation" && !c.ok) && checks.every((c) => c.ok || c.name === "evaluation") && (
        <button type="button" className="small danger" onClick={async () => {
          const reason = await promptDialog({ title: "Publish although the release gate fails?", body: "The reason goes to the audit log.", confirm: "Publish anyway", danger: true, input: { label: "Reason", required: true } });
          if (reason?.trim()) publish.mutate(reason.trim());
        }}>Publish anyway…</button>
      )}
      {checks && (
        <ul className="checklist small" data-testid="publish-checklist">
          {checks.map((c) => (
            <li key={c.name}>{c.ok ? "✅" : "❌"} <b>{c.name}</b> — {c.detail}
              {(c.items ?? []).length > 0 && <ul>{(c.items ?? []).map((i) => <li key={i}>{i}</li>)}</ul>}</li>
          ))}
        </ul>
      )}
      {key && (
        <div className="key-box" data-testid="api-key">
          <p className="small"><b>API key</b> (shown once — copy it now):</p>
          <code className="mono">{key.key}</code>
          <pre className="json small">{`curl -X POST ${location.origin}/api/v1/deployments/${key.deployment}/runs/wait \\
  -H "Authorization: Bearer ${key.key}" -H "content-type: application/json" \\
  -d '{"input": {}}'`}</pre>
        </div>
      )}
      <ul className="version-list">
        {(deps.data ?? []).map((d) => (
          <li key={d.id} data-testid={`deployment-${d.environment}`}>
            <div className="row gap">
              <b>{d.environment}</b> v{d.semver} {!d.active && <span className="badge warn">unpublished</span>}
              <code className="small">{d.key_prefix}…</code>
              <span className="spacer" />
              {d.active && can("builder") && <button type="button" className="small" onClick={() => void rotate(d)}>Rotate key</button>}
              {d.active && can("builder") && <button type="button" className="small danger" onClick={() => void unpublish(d)}>Unpublish</button>}
            </div>
            {d.active && <ConnectSection deployment={d} apiKey={key?.deployment === d.id ? key.key : undefined} />}
            {d.active && can("builder") && <ShareSection deployment={d} />}
          </li>
        ))}
      </ul>
    </section>
  );
}
