/** A subgraph's embedded workflow in the inspector: a summary, Open (drill in) and Inline (M2-T13). */
import type { FieldProps } from "@rjsf/utils";
import { useEditor } from "../editor/store";

export function SubgraphField(props: FieldProps) {
  const inner = (props.formData ?? {}) as { nodes?: unknown[]; resources?: unknown[] };
  const nodeId = useEditor((s) => s.selected);
  const drill = useEditor((s) => s.drill);
  return (
    <fieldset className="schema-field">
      <legend>{props.schema.title}</legend>
      <p className="muted small">
        {inner.nodes?.length ?? 0} step(s){inner.resources?.length ? `, ${inner.resources.length} resource(s)` : ""} inside.
      </p>
      <div className="row gap">
        <button type="button" disabled={!nodeId} onClick={() => nodeId && useEditor.getState().setDrill([...drill, nodeId])}>
          Open
        </button>
        <button type="button" disabled={!nodeId || drill.length > 0} title="Put the steps back on this canvas"
          onClick={() => nodeId && void useEditor.getState().refactor("inline", { node_id: nodeId }).then(() => useEditor.getState().select(null))}>
          Inline steps
        </button>
      </div>
    </fieldset>
  );
}
