/** Presence (G-4): avatars of the others who have this workflow open, a mark on the blocks they select, and a notice
 * when one of them saved a newer version. */
import { useMemo } from "react";
import { useMe } from "../api/session";
import { colorOf, initials, others, savedNewer, selectingBlock, usePresence } from "./presence";
import { hasUnsavedEdits, useEditor } from "./store";

const SHOWN = 5;

/** The name of a block (step or resource) by id, for "Ada is selecting Classify". */
function blockName(id: string): string {
  const wf = useEditor.getState().wf;
  const n = wf?.nodes.find((x) => x.id === id);
  if (n) return n.label || n.name;
  return wf?.resources.find((r) => r.id === id)?.name ?? id;
}

export function PresenceBar() {
  const peers = usePresence((s) => s.peers);
  const session = usePresence((s) => s.session);
  const me = useMe((s) => s.me.id);
  const here = useMemo(() => others(peers, session, me), [peers, session, me]);
  if (!here.length) return null;
  return (
    <div className="presence" role="group" aria-label={`${here.length} other${here.length === 1 ? "" : "s"} here`} data-testid="presence">
      {here.slice(0, SHOWN).map((p) => (
        <button type="button" key={p.user_id} className="avatar" style={{ background: colorOf(p.user_id) }}
          title={`${p.name}${p.selected.length ? ` is selecting ${p.selected.map(blockName).join(", ")}` : " has this workflow open"}`}
          onClick={() => { if (p.selected[0]) useEditor.getState().focusNode?.(p.selected[0]); }}>
          {initials(p.name)}
        </button>
      ))}
      {here.length > SHOWN && <span className="avatar more" title={here.slice(SHOWN).map((p) => p.name).join(", ")}>+{here.length - SHOWN}</span>}
    </div>
  );
}

/** A coloured mark on a block that others have selected. */
export function PeerMarks({ id }: { id: string }) {
  const peers = usePresence((s) => s.peers);
  const session = usePresence((s) => s.session);
  const who = useMemo(() => selectingBlock(peers, session, id), [peers, session, id]);
  if (!who.length) return null;
  return (
    <span className="peer-marks" aria-label={`selected by ${who.map((p) => p.name).join(", ")}`}>
      {who.map((p) => <b key={p.session} style={{ background: colorOf(p.user_id) }} title={`${p.name} is selecting this block`}>{initials(p.name)}</b>)}
    </span>
  );
}

/** "Ada saved a newer version": offers to reload (edits not saved yet are put back on top by the editor's own
 * conflict handling, which stays as it was). */
export function NewerVersionNotice() {
  const peers = usePresence((s) => s.peers);
  const session = usePresence((s) => s.session);
  const version = useEditor((s) => s.version);
  useEditor((s) => s.pending.length); // redraw as edits are queued and saved: the button says which it will do
  const ahead = useMemo(() => savedNewer(peers, session, version), [peers, session, version]);
  if (!ahead.length || !version) return null;
  const who = [...new Set(ahead.map((p) => p.name))];
  return (
    <div className="toast info presence-notice" role="status" data-testid="newer-version">
      {who.slice(0, 2).join(" and ")}{who.length > 2 ? ` and ${who.length - 2} more` : ""} saved a newer version (v{ahead[0].version}; you have v{version}).{" "}
      <button type="button" className="link" onClick={() => {
        const { workflowId, load, flush } = useEditor.getState();
        if (hasUnsavedEdits()) void flush(); // the save is refused as out of date: the editor reloads and re-applies your edits
        else if (workflowId) void load(workflowId);
      }}>{hasUnsavedEdits() ? "Save and update" : "Update"}</button>
    </div>
  );
}
