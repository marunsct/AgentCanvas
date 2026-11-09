/** Add or edit an evaluator with a form per kind (built from the kind's config schema, GET /evaluator-kinds)
 * instead of typing its config as JSON. */
import Form from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { EvaluatorKindOut, EvaluatorOut } from "../api/generated";
import { toast } from "../ui/dialog";
import { BUTTON_TEMPLATES, withItemDefaults } from "../ui/rjsfTemplates";
import { validator } from "../ui/validator";

/** Pydantic writes an optional field as anyOf [X, null]: a form wants X (left empty means null). */
export function formSchema(schema: unknown): RJSFSchema {
  const walk = (s: unknown): unknown => {
    if (Array.isArray(s)) return s.map(walk);
    if (!s || typeof s !== "object") return s;
    const o = s as Record<string, unknown>;
    if (Array.isArray(o.anyOf)) {
      const real = o.anyOf.filter((x) => !(x && typeof x === "object" && (x as { type?: string }).type === "null"));
      if (real.length === 1) {
        const { anyOf: _drop, ...rest } = o;
        return walk({ ...(real[0] as object), ...rest });
      }
    }
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(o)) out[k] = walk(v);
    if (out.default === null) delete out.default;
    return out;
  };
  return withItemDefaults(walk(schema)) as RJSFSchema;
}

export function useEvaluatorKinds() {
  return useQuery({ queryKey: ["evaluator-kinds"], queryFn: () => apiFetch<EvaluatorKindOut[]>("/evaluator-kinds"), staleTime: Infinity });
}

export function EvaluatorForm({ wfId, editing, onDone }: { wfId: string; editing?: EvaluatorOut; onDone: () => void }) {
  const qc = useQueryClient();
  const kinds = useEvaluatorKinds();
  const [name, setName] = useState(editing?.name ?? "");
  const [kind, setKind] = useState<string>(editing?.kind ?? "exact");
  const [config, setConfig] = useState<Record<string, unknown>>((editing?.config as Record<string, unknown>) ?? {});
  const [threshold, setThreshold] = useState(editing?.threshold != null ? String(editing.threshold) : "");
  const current = kinds.data?.find((k) => k.kind === kind);
  const save = useMutation({
    mutationFn: () => {
      const body = { config, threshold: threshold.trim() ? Number(threshold) : null };
      return editing
        ? apiFetch<EvaluatorOut>(`/evaluators/${editing.id}`, { method: "PATCH", json: { ...body, name } })
        : apiFetch<EvaluatorOut>(`/workflows/${wfId}/evaluators`, { method: "POST", json: { ...body, name, kind } });
    },
    onSuccess: () => {
      toast(editing ? `Saved ${name}` : `Added ${name}`);
      void qc.invalidateQueries({ queryKey: ["evaluators", wfId] });
      onDone();
    },
  });
  return (
    <form className="policy-form evaluator-form" aria-label={editing ? "Edit evaluator" : "New evaluator"}
      onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="row gap wrap">
        <input required placeholder="Evaluator name" aria-label="evaluator name" value={name} onChange={(e) => setName(e.target.value)} />
        <select aria-label="evaluator kind" value={kind} disabled={Boolean(editing)}
          onChange={(e) => { setKind(e.target.value); setConfig({}); }}>
          {kinds.data?.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
        </select>
        <input aria-label="threshold" placeholder={`pass at ${current?.default_threshold ?? 1}`} value={threshold} inputMode="decimal"
          onChange={(e) => setThreshold(e.target.value)} style={{ width: 130 }} title="the score (0 to 1) at which a result passes" />
      </div>
      {current && <p className="muted small">{current.description}</p>}
      {current && (
        <Form key={kind} schema={formSchema(current.config_schema)} validator={validator} formData={config}
          templates={{ ButtonTemplates: BUTTON_TEMPLATES }} uiSchema={{ "ui:submitButtonOptions": { norender: true } }}
          onChange={(e) => setConfig((e.formData ?? {}) as Record<string, unknown>)} />
      )}
      {kinds.isError && <p className="error-text small">Could not load the evaluator kinds: {(kinds.error as Error).message}</p>}
      <div className="row gap">
        <button type="submit" className="primary" disabled={save.isPending || !name.trim()}>{editing ? "Save evaluator" : "Add evaluator"}</button>
        <button type="button" onClick={onDone}>Cancel</button>
      </div>
      {save.error && <p role="alert" className="error-text">{(save.error as Error).message}</p>}
    </form>
  );
}
