/** Project secrets (M5-T6): write-only values; reference them in configs as {"$secret": "NAME"}. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import { PROJECT_ID } from "../api/queries";

export function SecretsPanel() {
  const qc = useQueryClient();
  const base = `/projects/${PROJECT_ID}/secrets`;
  const list = useQuery({ queryKey: ["secrets"], queryFn: () => apiFetch<{ name: string }[]>(base) });
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const refresh = () => void qc.invalidateQueries({ queryKey: ["secrets"] });
  const save = useMutation({
    mutationFn: () => apiFetch(`${base}/${encodeURIComponent(name)}`, { method: "PUT", json: { value } }),
    onSuccess: () => { setName(""); setValue(""); refresh(); },
  });
  const remove = useMutation({ mutationFn: (n: string) => apiFetch(`${base}/${encodeURIComponent(n)}`, { method: "DELETE" }), onSuccess: refresh });
  return (
    <section className="secrets" aria-label="secrets">
      <h4>Secrets</h4>
      <ul>
        {list.data?.map((s) => (
          <li key={s.name}>
            <code>{s.name}</code>
            <button type="button" className="link" aria-label={`delete secret ${s.name}`} onClick={() => remove.mutate(s.name)}>×</button>
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => { e.preventDefault(); if (name && value) save.mutate(); }}>
        <input placeholder="OPENAI_API_KEY" value={name} aria-label="secret name"
          onChange={(e) => setName(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_"))} />
        <input type="password" placeholder="value (write-only)" value={value} aria-label="secret value" autoComplete="off"
          onChange={(e) => setValue(e.target.value)} />
        <button type="submit" disabled={!name || !value || save.isPending}>Save secret</button>
      </form>
      {save.error && <p className="error-text small">{(save.error as Error).message}</p>}
      <p className="muted small">Use in configs as <code>{'{"$secret": "NAME"}'}</code>, or <code>secret("NAME")</code> in scripts.</p>
    </section>
  );
}
