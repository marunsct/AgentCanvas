/** Problems panel (M2-T9): diagnostics from every save; click focuses the node, or the arrow. */
import { useEditor } from "../editor/store";
import { findElement } from "../ir/model";
import { diagnosticHelp } from "../help/diagnostics";
import { HelpText } from "../help/HelpText";
import { useCopilot } from "../copilot/copilot";

const ICON = { error: "⛔", warning: "⚠", info: "ℹ" } as const;

export function ProblemsPanel() {
  const wf = useEditor((s) => s.wf);
  const diagnostics = useEditor((s) => s.diagnostics);
  const focusNode = useEditor((s) => s.focusNode);
  const dispatch = useEditor((s) => s.dispatch);
  const counts = { error: 0, warning: 0, info: 0 };
  for (const d of diagnostics) counts[d.severity] += 1;
  return (
    <div className="problems" aria-label="Problems">
      <div className="problems-head">
        Problems <span className="badge err">{counts.error}</span> <span className="badge warn">{counts.warning}</span>{" "}
        <span className="badge">{counts.info}</span>
        {counts.error > 0 && (
          <button type="button" className="link small" data-testid="ask-copilot-fix"
            onClick={() => useCopilot.getState().askFor("Find and fix the problems in this workflow.", "build")}>
            ✦ Ask the Copilot to fix these
          </button>
        )}
      </div>
      {counts.error + counts.warning === 0 && <p className="muted ok-text">No problems — the workflow can run.</p>}
      {diagnostics.length > 0 && (
        <ul>
          {diagnostics.map((d, i) => {
            const el = d.node_id && wf ? findElement(wf, d.node_id) : undefined;
            // a problem with an arrow (E006, W012) used to be a disabled button: show the arrow, offer to remove it
            const edge = !el && d.edge_id ? wf?.edges.find((e) => e.id === d.edge_id) : undefined;
            const ends = edge && wf ? `${findElement(wf, edge.source.node)?.name ?? edge.source.node} → ${findElement(wf, edge.target.node)?.name ?? edge.target.node}` : "";
            const target = el?.id ?? edge?.id;
            return (
              <li key={i} className={d.severity}>
                <button type="button" className="link" disabled={!target} onClick={() => target && focusNode?.(target)}>
                  {ICON[d.severity]} <b>{d.code}</b> {el && <code>{el.name}</code>}{edge && <code>{ends}</code>} {d.message}
                  {d.hint && <span className="muted"> — {d.hint}</span>}
                </button>
                {edge && (
                  <button type="button" className="link small" onClick={() => dispatch([{ op: "Disconnect", id: edge.id }])}>
                    Remove this connection
                  </button>
                )}
                <HelpText help={diagnosticHelp(d)} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
