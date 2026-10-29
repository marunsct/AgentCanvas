/** Right-panel inspector: the selected node/resource, or the workflow when nothing is selected. */
import { diagnosticHelp } from "../help/diagnostics";
import { HelpText } from "../help/HelpText";
import { useManifests } from "../api/queries";
import { DesignedAgent, EnginePicker } from "../editor/Frameworks";
import { useEditor } from "../editor/store";
import { useState } from "react";
import { findElement, isResource, type IRNode, type IRResource, nameProblem } from "../ir/model";
import { ConfigForm } from "./ConfigForm";
import { MockPanel, PinPanel } from "./DebugAids";
import { McpBrowser } from "./McpBrowser";
import { ScriptPanel } from "./ScriptPanel";
import { PoliciesEditor } from "./PoliciesEditor";
import { WiredPorts } from "./WiredPorts";
import { WorkflowPanel } from "./WorkflowPanel";
import { useCopilot } from "../copilot/copilot";
import { useCan } from "../api/session";

/** The name field: a name that cannot be used says why and stays for fixing (it used to be dropped on blur). */
function NameField({ el, taken, onRename }: { el: IRNode | IRResource; taken: string[]; onRename: (name: string) => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <label>
      Name (letters, digits and _ only; used to refer to it)
      <input className="mono" defaultValue={el.name} aria-label="node name" aria-invalid={Boolean(problem)}
        aria-describedby={problem ? `name-problem-${el.id}` : undefined}
        onChange={() => setProblem(null)}
        onBlur={(e) => {
          const v = e.target.value;
          const p = v === el.name ? null : nameProblem(v, taken);
          setProblem(p);
          if (!p && v !== el.name) onRename(v);
        }} />
      {problem && <span id={`name-problem-${el.id}`} className="field-error" role="alert">{problem}</span>}
    </label>
  );
}

export function Inspector() {
  const wf = useEditor((s) => s.wf);
  const selected = useEditor((s) => s.selected);
  const diagnostics = useEditor((s) => s.diagnostics);
  const dispatch = useEditor((s) => s.dispatch);
  const breakpoints = useEditor((s) => s.breakpoints);
  const manifests = useManifests();
  const canBuild = useCan("builder");
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
      <h3>{m?.label ?? el.type} <small className="muted" title="internal type id">{el.type}</small></h3>
      {m?.description && <p className="muted">{m.description}</p>}
      <div className="row gap ask-copilot" data-testid="ask-copilot-block">
        {/* the block's name and id go with the question, so the Copilot looks at this one */}
        <button type="button" className="link small" onClick={() => useCopilot.getState().askFor(
          `Explain the block "${el.label || el.name}" (id ${el.id}): what it does here, what it reads and writes, and anything I should change.`, "ask")}>
          ✦ Ask the Copilot about this block</button>
        {canBuild && mine.some((d) => d.severity === "error") && (
          <button type="button" className="link small" onClick={() => useCopilot.getState().askFor(
            `Fix the problems of the block "${el.label || el.name}" (id ${el.id}).`, "build")}>Fix with the Copilot</button>
        )}
      </div>
      <NameField el={el} onRename={(name) => update({ name })}
        taken={[...wf.nodes, ...wf.resources].filter((x) => x.id !== el.id).map((x) => x.name)} />
      {!res && (
        <label>
          Label
          <input defaultValue={el.label ?? ""} aria-label="node label"
            onBlur={(e) => e.target.value !== (el.label ?? "") && update({ label: e.target.value || null })} />
        </label>
      )}
      {!res && <EnginePicker node={el as IRNode} />}
      {!res && <DesignedAgent node={el as IRNode} />}
      {mine.length > 0 && (
        <ul className="diag-list">
          {mine.map((d, i) => (
            <li key={i} className={d.severity}>
              <b>{d.code}</b> {d.message}{d.hint ? ` — ${d.hint}` : ""}
              <HelpText help={diagnosticHelp(d)} />
            </li>
          ))}
        </ul>
      )}
      {res && el.type === "mcp.server" && <McpBrowser resource={el} />}
      {!res && el.type === "core.script" && <ScriptPanel node={el} />}
      {m && <ConfigForm element={el} manifest={m} isResource={res} />}
      {m && <WiredPorts element={el} manifest={m} />}
      {!res && (
        <button type="button" onClick={() => useEditor.getState().toggleBreakpoint(el.id)}
          title="Debug runs pause before this node">
          {breakpoints.includes(el.id) ? "● Remove breakpoint" : "○ Add breakpoint"}
        </button>
      )}
      {!res && <PinPanel node={el} />}
      {res && el.type === "model.chat" && <MockPanel resource={el} key={`mock-${el.name}`} />}
      {!res && <PoliciesEditor node={el} />}
      <button type="button" className="danger"
        onClick={() => {
          dispatch([res ? { op: "RemoveResource", id: el.id } : { op: "RemoveNode", id: el.id }]);
          const st = useEditor.getState();
          st.select(null);
          // it can be undone, so it is not asked first: say so (a refused delete says why instead)
          if (!st.notice && st.wf && !findElement(st.wf, el.id)) {
            st.setNotice({ kind: "info", text: `Deleted ${el.name}. Undo with Ctrl/⌘+Z.` });
          }
        }}>
        Delete {res ? "resource" : "node"}
      </button>
    </div>
  );
}
