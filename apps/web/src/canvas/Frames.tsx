/** Visual groups on the canvas: frames behind their member steps, with an editable label (layout only). */
import { useNodes, ViewportPortal } from "@xyflow/react";
import { ulid } from "ulid";
import { useEditor } from "../editor/store";
import { addGroup, frameAround, groupsOf, removeGroup, renameGroup } from "./groups";

export function Frames() {
  const wf = useEditor((s) => s.wf);
  const drill = useEditor((s) => s.drill);
  const nodes = useNodes(); // re-renders while steps move, so frames follow
  if (!wf || drill.length) return null;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const dispatch = useEditor.getState().dispatch;
  return (
    <ViewportPortal>
      {groupsOf(wf).map((g) => {
        const rects = g.members.flatMap((id) => {
          const n = byId.get(id);
          return n ? [{ x: n.position.x, y: n.position.y, width: n.measured?.width ?? 200, height: n.measured?.height ?? 60 }] : [];
        });
        const f = frameAround(rects);
        if (!f) return null;
        return (
          <div key={g.id} className="frame" data-testid={`frame-${g.label}`}
            style={{ transform: `translate(${f.x}px, ${f.y}px)`, width: f.width, height: f.height, borderColor: g.color, background: `${g.color}0d` }}>
            <div className="frame-label" style={{ color: g.color }}>
              <span title="double-click to rename" onDoubleClick={() => {
                const label = window.prompt("Frame label", g.label)?.trim();
                if (label && label !== g.label) dispatch([{ op: "UpdateMeta", metadata: renameGroup(wf, g.id, label) }]);
              }}>{g.label}</span>
              <button type="button" className="link small" aria-label={`remove frame ${g.label}`}
                onClick={() => dispatch([{ op: "UpdateMeta", metadata: removeGroup(wf, g.id) }])}>✕</button>
            </div>
          </div>
        );
      })}
    </ViewportPortal>
  );
}

/** Frame the selected steps (toolbar "▭ Frame"); returns false when nothing is selected. */
export function frameSelection(selectedIds: string[], label: string): boolean {
  const st = useEditor.getState();
  if (!st.wf || !selectedIds.length) return false;
  st.dispatch([{ op: "UpdateMeta", metadata: addGroup(st.wf, `grp_${ulid()}`, label, selectedIds) }]);
  return true;
}
