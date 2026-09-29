/** Workflow-level settings shown when nothing is selected. */
import { useEditor } from "../editor/store";
import { StateDesigner } from "./StateDesigner";

export function WorkflowPanel() {
  const wf = useEditor((s) => s.wf);
  const dispatch = useEditor((s) => s.dispatch);
  if (!wf) return null;
  const s = wf.settings;
  return (
    <div className="panel-body">
      <h3>Workflow</h3>
      <label>
        Name
        <input defaultValue={wf.name} key={`name-${wf.name}`}
          onBlur={(e) => e.target.value.trim() && e.target.value !== wf.name && dispatch([{ op: "UpdateMeta", name: e.target.value.trim() }])} />
      </label>
      <label>
        Description
        <textarea defaultValue={wf.description} rows={2} key={`desc-${wf.description}`}
          onBlur={(e) => e.target.value !== wf.description && dispatch([{ op: "UpdateMeta", description: e.target.value }])} />
      </label>
      <div className="row gap">
        <label>
          Recursion limit
          <input type="number" min={1} max={10000} value={s.recursion_limit ?? 50}
            onChange={(e) => dispatch([{ op: "UpdateSettings", patch: { recursion_limit: Number(e.target.value) || 50 } }], { debounceMs: 400 })} />
        </label>
        <label>
          Durability
          <select value={s.durability ?? "async"} onChange={(e) => dispatch([{ op: "UpdateSettings", patch: { durability: e.target.value } }])}>
            <option value="exit">exit (fastest)</option>
            <option value="async">async (default)</option>
            <option value="sync">sync (side effects)</option>
          </select>
        </label>
      </div>
      <StateDesigner />
    </div>
  );
}
