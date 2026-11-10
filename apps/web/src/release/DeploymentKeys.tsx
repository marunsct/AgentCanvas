/** A deployment's API keys (E-8): several at once, each named and optionally expiring, so a key is replaced
 * without downtime (add the new one, move the callers, revoke the old one). A key is shown once, when made; the
 * list says when each was last used. Builders only. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { DeploymentKeyOut, DeploymentOut, KeyCreated } from "../api/generated";
import { tokenState } from "../settings/ApiTokens";
import { confirmDialog, toast } from "../ui/dialog";
import { ago, exact } from "../ui/time";

const EXPIRY: [string, number | null][] = [["Never", null], ["30 days", 30], ["90 days", 90], ["1 year", 365]];

export function DeploymentKeys({ deployment, onNewKey }: { deployment: DeploymentOut; onNewKey: (key: string) => void }) {
  const qc = useQueryClient();
  const queryKey = ["deployment-keys", deployment.id];
  const keys = useQuery({ queryKey, queryFn: () => apiFetch<DeploymentKeyOut[]>(`/deployments/${deployment.id}/keys`) });
  const [name, setName] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [showOld, setShowOld] = useState(false);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey });
    void qc.invalidateQueries({ queryKey: ["deployments"] });
  };
  const create = useMutation({
    mutationFn: () => apiFetch<KeyCreated>(`/deployments/${deployment.id}/keys`, { method: "POST", json: { name: name.trim(), expires_in_days: days } }),
    onSuccess: (k) => { onNewKey(k.api_key); setName(""); refresh(); },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => apiFetch(`/deployments/${deployment.id}/keys/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => { toast("Revoked the key"); refresh(); },
  });
  const ask = async (k: DeploymentKeyOut) => {
    if (await confirmDialog({ title: `Revoke "${k.name}"?`, body: "Calls with this key are refused at once.", confirm: "Revoke", danger: true })) {
      revoke.mutate(k.id);
    }
  };
  const all = keys.data ?? [];
  const working = all.filter((k) => tokenState(k) === "active");
  const shown = showOld ? all : working;
  return (
    <details className="deployment-keys" data-testid={`keys-${deployment.environment}`}>
      <summary>API keys ({working.length} working)</summary>
      {keys.error && <p className="error-text small" role="alert">Could not load the keys: {(keys.error as Error).message}</p>}
      {shown.length > 0 && (
        <table className="table small" aria-label={`${deployment.environment} keys`}>
          <thead><tr><th>Name</th><th>Key</th><th>Expires</th><th>Last used</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {shown.map((k) => {
              const state = tokenState(k);
              return (
                <tr key={k.id} className={state === "active" ? "" : "muted"}>
                  <td>{k.name}</td>
                  <td><code>{k.prefix}…</code></td>
                  <td title={k.expires_at ? exact(k.expires_at) : undefined}>
                    {state !== "active" ? state : k.expires_at ? new Date(k.expires_at).toLocaleString() : "never"}
                  </td>
                  <td>{k.last_used_at ? ago(k.last_used_at) : "never"}</td>
                  <td className="actions">{state === "active" && (
                    <button type="button" className="link small" aria-label={`revoke ${k.name}`} disabled={revoke.isPending}
                      onClick={() => void ask(k)}>Revoke</button>
                  )}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {all.length > working.length && (
        <button type="button" className="link small" onClick={() => setShowOld((v) => !v)}>
          {showOld ? "Hide revoked and expired keys" : `Show revoked and expired keys (${all.length - working.length})`}
        </button>
      )}
      <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <input placeholder="Who uses it, e.g. website backend" value={name} maxLength={100} aria-label="key name"
          onChange={(e) => setName(e.target.value)} />
        <label className="row small">Expires
          <select aria-label="key expiry" value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
            {EXPIRY.map(([label, n]) => <option key={label} value={n ?? ""}>{label}</option>)}
          </select>
        </label>
        <button type="submit" className="small" disabled={!name.trim() || create.isPending}>Add key</button>
      </form>
      {create.error && <p className="error-text small" role="alert">Could not add the key: {(create.error as Error).message}</p>}
      {revoke.error && <p className="error-text small" role="alert">Could not revoke the key: {(revoke.error as Error).message}</p>}
    </details>
  );
}
