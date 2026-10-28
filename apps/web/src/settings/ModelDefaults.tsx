/** Settings › Models › By purpose: the models used when nothing names one (judges, simulated users, embeddings),
 * with the rule written out and, for each purpose, the model it comes to and why. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { ModelDefaultsOut } from "../api/generated";
import { PROJECT_ID, useModels } from "../api/queries";
import { useCan } from "../api/session";
import { toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

const EDITABLE = [
  { purpose: "judge", label: "Judge evaluators", hint: "LLM-as-judge and pairwise evaluators saved without a model" },
  { purpose: "simulated_user", label: "Simulated users", hint: "the user a multi-turn experiment plays" },
  { purpose: "embedding", label: "Knowledge embeddings", hint: "new knowledge bases; empty = the built-in offline embedding" },
] as const;

export function ModelDefaults() {
  const qc = useQueryClient();
  const canBuild = useCan("builder");
  const models = useModels();
  const url = `/projects/${PROJECT_ID}/model-defaults`;
  const q = useQuery({ queryKey: ["model-defaults", PROJECT_ID], queryFn: () => apiFetch<ModelDefaultsOut>(url) });
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { if (q.data) setDraft({ ...(q.data.defaults as Record<string, string>) }); }, [q.data]);
  const save = useMutation({
    mutationFn: () => apiFetch<ModelDefaultsOut>(url, {
      method: "PUT",
      json: Object.fromEntries(EDITABLE.map(({ purpose }) => [purpose, draft[purpose]?.trim() || null])),
    }),
    onSuccess: (out) => { qc.setQueryData(["model-defaults", PROJECT_ID], out); toast("Saved the default models"); },
  });
  if (!q.data) return <section className="settings-card"><h2>By purpose</h2><QueryStatus q={q} what="the default models" /></section>;
  const resolved = Object.fromEntries(q.data.resolved.map((r) => [r.purpose, r]));
  return (
    <section className="settings-card" aria-label="Models by purpose">
      <h2>By purpose</h2>
      <p className="muted small">
        A block, evaluator or knowledge base that names its model uses it. Otherwise the default for its purpose below,
        then the project's model above. Defaults apply when something is saved, so results keep saying which model
        produced them.
      </p>
      <datalist id="purpose-models">
        {(models.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </datalist>
      <table className="table">
        <thead><tr><th>Purpose</th><th>Default</th><th>Used now</th></tr></thead>
        <tbody>
          <tr>
            <td>Model blocks, Playground</td>
            <td className="muted small">the project's model</td>
            <td><Used r={resolved.chat} /></td>
          </tr>
          {EDITABLE.map(({ purpose, label, hint }) => (
            <tr key={purpose}>
              <td>{label}<div className="muted small">{hint}</div></td>
              <td>
                <input className="mono" list="purpose-models" aria-label={`default model for ${label}`} disabled={!canBuild}
                  placeholder={purpose === "embedding" ? "builtin" : "the project's model"} value={draft[purpose] ?? ""}
                  onChange={(e) => setDraft({ ...draft, [purpose]: e.target.value })} />
              </td>
              <td><Used r={resolved[purpose]} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {canBuild
        ? <button type="button" className="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save default models</button>
        : <p className="muted small">Your role can see these; a builder or admin can change them.</p>}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </section>
  );
}

function Used({ r }: { r?: { model?: string | null; origin: string; problem?: string | null } }) {
  if (!r) return null;
  if (!r.model) return <span className="warn-text small">{r.problem}</span>;
  return <span title={`from ${r.origin}`}><code>{r.model}</code><div className="muted small">{r.origin}</div></span>;
}
