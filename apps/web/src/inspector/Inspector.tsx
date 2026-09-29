/** Right-panel inspector: the selected node/resource, or the workflow when nothing is selected. */
import { useManifests } from "../api/queries";
import { useEditor } from "../editor/store";
import { findElement, isResource } from "../ir/model";
import { ConfigForm } from "./ConfigForm";
import { PoliciesEditor } from "./PoliciesEditor";
import { WorkflowPanel } from "./WorkflowPanel";

const NAME = /^[a-z][a-z0-9_]{0,62}$/;

export function Inspector() {
  const wf = useEditor((s) => s.wf);
  const selected = useEditor((s) => s.selected);
  const diagnostics = useEditor((s) => s.diagnostics);
  const dispatch = useEditor((s) => s.dispatch);
  const breakpoints = useEditor((s) => s.breakpoints);
  const manifests = useManifests();
  if (!wf) return null;
  const el = selected ? findElement(wf, selected) : undefined;
  if (!el) return <WorkflowPanel />;
  const res = isResource(wf, el.id);
  const m = manifests.get(el.type);
  const mine = diagnostics.filter((d) => d.node_id === el.id);
  const update = (patch: Record<string, unknown>) =>
    dispatch([res ? { op: "UpdateResource", id: el.id, patch } : { op: "UpdateNode", id: el.id, patch }]);

  return (
    <div className="panel-body" key={el.id}>
      <h3>{m?.label ?? el.type} <small className="muted">{el.type}</small></h3>
      {m?.description && <p className="muted">{m.description}</p>}
      <label>
        Name (code identifier)
        <input className="mono" defaultValue={el.name} aria-label="node name"
          onBlur={(e) => { const v = e.target.value; if (v !== el.name && NAME.test(v)) update({ name: v }); }} />
      </label>
      {!res && (
        <label>
          Label
          <input defaultValue={el.label ?? ""} aria-label="node label"
            onBlur={(e) => e.target.value !== (el.label ?? "") && update({ label: e.target.value || null })} />
        </label>
      )}
      {mine.length > 0 && (
        <ul className="diag-list">
          {mine.map((d, i) => <li key={i} className={d.severity}><b>{d.code}</b> {d.message}{d.hint ? ` — ${d.hint}` : ""}</li>)}
        </ul>
      )}
      {m && <ConfigForm element={el} manifest={m} isResource={res} />}
      {!res && (
        <button type="button" onClick={() => useEditor.getState().toggleBreakpoint(el.id)}
          title="Debug runs pause before this node">
          {breakpoints.includes(el.id) ? "● Remove breakpoint" : "○ Add breakpoint"}
        </button>
      )}
      {!res && <PoliciesEditor node={el} />}
      <button type="button" className="danger"
        onClick={() => { dispatch([res ? { op: "RemoveResource", id: el.id } : { op: "RemoveNode", id: el.id }]); useEditor.getState().select(null); }}>
        Delete {res ? "resource" : "node"}
      </button>
    </div>
  );
}
