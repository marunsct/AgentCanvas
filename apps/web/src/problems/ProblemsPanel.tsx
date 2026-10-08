/** Problems panel (M2-T9): diagnostics from every save; click focuses the node. */
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
      {diagnostics.length === 0 ? (
        <p className="muted ok-text">No problems — the workflow can run.</p>
      ) : (
        <ul>
          {diagnostics.map((d, i) => {
            const el = d.node_id && wf ? findElement(wf, d.node_id) : undefined;
            return (
              <li key={i} className={d.severity}>
                <button type="button" className="link" disabled={!el} onClick={() => el && focusNode?.(el.id)}>
                  {ICON[d.severity]} <b>{d.code}</b> {el && <code>{el.name}</code>} {d.message}
                  {d.hint && <span className="muted"> — {d.hint}</span>}
                </button>
                <HelpText help={diagnosticHelp(d)} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
