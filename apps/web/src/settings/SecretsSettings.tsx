/** Project secrets (M5-T6): write-only values that configs reference as {"$secret": "NAME"} and scripts as
 * secret("NAME"). Values are never shown again; a secret can be replaced or deleted. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";
import { useCan } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

export function SecretsSettings() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const base = `/projects/${PROJECT_ID}/secrets`;
  const list = useQuery({ queryKey: ["secrets"], queryFn: () => apiFetch<{ name: string }[]>(base) });
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const refresh = () => void qc.invalidateQueries({ queryKey: ["secrets"] });
  const exists = list.data?.some((s) => s.name === name) ?? false;
  const save = useMutation({
    mutationFn: () => apiFetch(`${base}/${encodeURIComponent(name)}`, { method: "PUT", json: { value } }),
    onSuccess: () => { toast(exists ? `Replaced ${name}` : `Saved ${name}`); setName(""); setValue(""); refresh(); },
  });
  const remove = useMutation({
    mutationFn: (n: string) => apiFetch(`${base}/${encodeURIComponent(n)}`, { method: "DELETE" }),
    onSuccess: (_, n) => { toast(`Deleted ${n}`); refresh(); },
  });
  return (
    <section className="settings-card secrets" aria-label="secrets">
      <h2>Secrets</h2>
      <p className="muted small">
        API keys and passwords your workflows use. Values are stored encrypted and never shown again. Use one in a block's
        configuration as <code>{'{"$secret": "NAME"}'}</code>, or as <code>secret("NAME")</code> in a script.
      </p>
      <QueryStatus q={list} what="the project's secrets" />
      {list.data && list.data.length === 0 && <p className="empty-state">No secrets yet.</p>}
      {list.data && list.data.length > 0 && (
        <table className="table">
          <thead><tr><th>Name</th><th>Value</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {list.data.map((s) => (
              <tr key={s.name}>
                <td><code>{s.name}</code></td>
                <td className="muted">set · hidden</td>
                <td className="actions">{canBuild && (
                  <button type="button" className="link small" aria-label={`delete secret ${s.name}`} disabled={remove.isPending}
                    onClick={async () => {
                      if (await confirmDialog({ title: `Delete the secret ${s.name}?`, body: "Blocks and scripts that use it will fail until it is set again.", confirm: "Delete", danger: true })) remove.mutate(s.name);
                    }}>Delete</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {remove.error && <p className="error-text small" role="alert">Could not delete the secret: {(remove.error as Error).message}</p>}
      {canBuild ? (
        <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); if (name && value) save.mutate(); }}>
          <input placeholder="OPENAI_API_KEY" value={name} aria-label="secret name" className="mono" autoComplete="off"
            onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
          <input type="password" placeholder="value (write-only)" value={value} aria-label="secret value" autoComplete="new-password"
            onChange={(e) => setValue(e.target.value)} />
          <button type="submit" className="primary" disabled={!name || !value || save.isPending}>{exists ? "Replace secret" : "Save secret"}</button>
        </form>
      ) : <p className="muted small">Your role can see which secrets exist; a builder or admin can add, replace or delete them.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save the secret: {(save.error as Error).message}</p>}
    </section>
  );
}
