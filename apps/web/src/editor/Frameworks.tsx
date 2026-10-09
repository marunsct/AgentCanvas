/** S20: which framework a workflow (and an AI block) is designed for, and what each installed framework can run. */
import { useMemo } from "react";
import { useFrameworks } from "../api/queries";
import type { IRNode } from "../ir/model";
import { ENGINE_TYPES, portability } from "../ir/portability";
import { useEditor } from "./store";

/** The workflow's framework, in the workflow settings (click empty canvas). */
export function FrameworkPicker() {
  const framework = useEditor((s) => s.wf?.framework ?? "langchain");
  const dispatch = useEditor((s) => s.dispatch);
  const { data: frameworks = [] } = useFrameworks();
  const current = frameworks.find((f) => f.id === framework);
  return (
    <label>
      Framework
      <select aria-label="framework" value={framework}
        onChange={(e) => dispatch([{ op: "UpdateMeta", framework: e.target.value }])}>
        {!current && <option value={framework}>{framework} (not installed)</option>}
        {frameworks.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
      </select>
      {current?.description && <small className="muted">{current.description}</small>}
    </label>
  );
}

/** One badge per installed framework: can this workflow run there, and which blocks stop it. */
export function FrameworkBadges() {
  const wf = useEditor((s) => s.wf);
  const select = useEditor((s) => s.select);
  const { data: frameworks = [] } = useFrameworks();
  const fits = useMemo(() => (wf ? portability(wf, frameworks) : []), [wf, frameworks]);
  if (!wf || fits.length === 0) return null;
  return (
    <div className="framework-badges" role="list" aria-label="frameworks this workflow can run on">
      {fits.map((f) => {
        const mine = f.id === wf.framework;
        const names = f.blockers.map((b) => b.name);
        const title = f.ok
          ? `${f.label} can run every block${f.emulated.length ? ` (imitates ${f.emulated.map((b) => b.name).join(", ")})` : ""}`
          : `${f.label} cannot run: ${names.join(", ")}`;
        return (
          <span key={f.id} role="listitem"><button type="button" title={title}
            className={`framework-badge ${f.ok ? "ok" : "blocked"}${mine ? " mine" : ""}`}
            aria-label={`${f.label}: ${f.ok ? "ready" : `${names.length} block${names.length === 1 ? "" : "s"} it cannot run`}${mine ? " (this workflow's framework)" : ""}`}
            onClick={() => f.blockers[0] && select(f.blockers[0].id)}>
            {f.ok ? "✓" : "✕"} {f.label}{f.ok ? "" : ` · ${names.length}`}
          </button></span>
        );
      })}
    </div>
  );
}

/** IR 1.2: an AI block may be designed for another framework than the workflow's. */
export function EnginePicker({ node }: { node: IRNode }) {
  const framework = useEditor((s) => s.wf?.framework ?? "langchain");
  const dispatch = useEditor((s) => s.dispatch);
  const { data: frameworks = [] } = useFrameworks();
  if (!ENGINE_TYPES.has(node.type) || (frameworks.length < 2 && !node.engine)) return null;
  const workflowLabel = frameworks.find((f) => f.id === framework)?.label ?? framework;
  return (
    <label>
      Designed for
      <select aria-label="block framework" value={node.engine ?? ""}
        onChange={(e) => dispatch([{ op: "UpdateNode", id: node.id, patch: { engine: e.target.value || null } }])}>
        <option value="">Same as the workflow ({workflowLabel})</option>
        {frameworks.filter((f) => f.id !== framework || node.engine === f.id).map((f) => (
          <option key={f.id} value={f.id}>{f.label}</option>
        ))}
      </select>
    </label>
  );
}
