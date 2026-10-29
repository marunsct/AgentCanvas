/** State designer (FR-STATE-01, M2-T8): channels with type, reducer, storage and visibility. */
import { useState } from "react";
import { useEditor } from "../editor/store";
import type { Command } from "../ir/commands";
import { schemaFor, typeLabel } from "../ir/files";
import { type Channel, nameProblem, type WF } from "../ir/model";

const TYPES = ["string", "number", "integer", "boolean", "array", "object", "file", "image"]; // file, image: G-5
const REDUCERS = ["replace", "append", "add_messages", "sum", "merge"] as const;
const IO = ["inout", "in", "out", "private"] as const;
const NAME = /^[a-z][a-z0-9_]{0,62}$/;
const NO_CHANNELS: Channel[] = [];

/** The edits that make every `state.<from>` in the blocks' settings (prompts, conditions) say `state.<to>`. */
export function renameStateRefs(wf: WF, from: string, to: string): Command[] {
  const ref = new RegExp(`\\bstate\\.${from}\\b`, "g"); // names are identifiers: nothing to escape
  const swap = (v: unknown): unknown => (typeof v === "string" ? v.replace(ref, `state.${to}`)
    : Array.isArray(v) ? v.map(swap)
    : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, swap(x)])) : v);
  const changed = <T extends { config?: unknown }>(el: T) => {
    const config = swap(el.config ?? {}) as T["config"];
    return JSON.stringify(config) === JSON.stringify(el.config ?? {}) ? null : { ...el, config };
  };
  return [
    ...wf.nodes.map(changed).filter((n) => n !== null).map((node): Command => ({ op: "ReplaceNode", node })),
    ...wf.resources.map(changed).filter((r) => r !== null).map((resource): Command => ({ op: "ReplaceResource", resource })),
  ];
}

export function StateDesigner() {
  const channels = useEditor((s) => s.wf?.state.channels ?? NO_CHANNELS);
  const dispatch = useEditor((s) => s.dispatch);
  // a renamed channel the blocks still refer to by its old name: offer to update them
  const [renamed, setRenamed] = useState<{ from: string; to: string; count: number } | null>(null);
  const commit = (next: Channel[], debounceMs = 0) => dispatch([{ op: "SetChannels", channels: next }], { debounceMs });
  const rename = (i: number, to: string) => {
    const from = channels[i].name;
    edit(i, { name: to });
    const wf = useEditor.getState().wf;
    const count = wf ? renameStateRefs(wf, from, to).length : 0;
    setRenamed(count ? { from, to, count } : null);
  };
  const updateRefs = () => {
    const wf = useEditor.getState().wf;
    if (wf && renamed) dispatch(renameStateRefs(wf, renamed.from, renamed.to));
    setRenamed(null);
  };
  const edit = (i: number, patch: Partial<Channel>, debounceMs = 0) => {
    const next = channels.map((c, j) => (j === i ? { ...c, ...patch } : c));
    if (patch.reducer && !["append", "add_messages"].includes(patch.reducer)) next[i] = { ...next[i], storage: "full" };
    if (next.every((c) => NAME.test(c.name))) commit(next, debounceMs);
  };
  const add = (c: Channel) => {
    if (channels.some((x) => x.name === c.name)) return;
    commit([...channels, c]);
  };
  const fresh = () => {
    let i = channels.length + 1;
    while (channels.some((c) => c.name === `field_${i}`)) i++;
    return `field_${i}`;
  };

  return (
    <section className="state-designer">
      <h4>State channels</h4>
      <table>
        <thead><tr><th>name</th><th>type</th><th>reducer</th><th>storage</th><th>io</th><th /></tr></thead>
        <tbody>
          {channels.map((c, i) => (
            <tr key={`${i}-${c.name}`}>
              <td><ChannelName value={c.name} taken={channels.filter((_, j) => j !== i).map((x) => x.name)}
                onCommit={(name) => rename(i, name)} /></td>
              <td>
                <select aria-label={`${c.name} type`} value={typeLabel(c.type)} onChange={(e) => edit(i, { type: schemaFor(e.target.value) })}
                  title="file and image: a person attaches one in the Run tab or on the share page">
                  {TYPES.map((t) => <option key={t}>{t}</option>)}
                </select>
              </td>
              <td>
                <select aria-label={`${c.name} reducer`} value={c.reducer ?? "replace"} onChange={(e) => edit(i, { reducer: e.target.value as Channel["reducer"] })}>
                  {REDUCERS.map((r) => <option key={r}>{r}</option>)}
                </select>
              </td>
              <td>
                <select aria-label={`${c.name} storage`} value={c.storage ?? "full"} title="delta = LangGraph DeltaChannel (append-heavy channels)"
                  disabled={!["append", "add_messages"].includes(c.reducer ?? "replace")}
                  onChange={(e) => edit(i, { storage: e.target.value as Channel["storage"] })}>
                  <option>full</option><option>delta</option>
                </select>
              </td>
              <td>
                <select aria-label={`${c.name} input or output`} value={c.io ?? "inout"} onChange={(e) => edit(i, { io: e.target.value as Channel["io"] })}>
                  {IO.map((x) => <option key={x}>{x}</option>)}
                </select>
              </td>
              <td><button type="button" className="icon-btn" aria-label={`remove ${c.name}`}
                onClick={() => commit(channels.filter((_, j) => j !== i))}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {renamed && (
        <p className="small" role="status" data-testid="rename-refs">
          {renamed.count} block{renamed.count === 1 ? "" : "s"} still use <code>state.{renamed.from}</code>.{" "}
          <button type="button" className="link" onClick={updateRefs}>Update {renamed.count === 1 ? "it" : "them"} to state.{renamed.to}</button>{" "}
          <button type="button" className="link" onClick={() => setRenamed(null)}>Leave as is</button>
        </p>
      )}
      <div className="row gap">
        <button type="button" onClick={() => add({ name: fresh() })}>+ Channel</button>
        <button type="button" title="messages with add_messages (chat workflows)"
          onClick={() => add({ name: "messages", type: { type: "array" }, reducer: "add_messages", storage: "delta" })}>
          + Chat preset
        </button>
      </div>
    </section>
  );
}

/** A channel name that cannot be used says why and stays for fixing (it used to be dropped on blur). */
function ChannelName({ value, taken, onCommit }: { value: string; taken: string[]; onCommit: (v: string) => void }) {
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <>
      <input className="mono" defaultValue={value} aria-label="channel name" aria-invalid={Boolean(problem)}
        onChange={() => setProblem(null)}
        onBlur={(e) => {
          const v = e.target.value;
          const p = v === value ? null : nameProblem(v, taken, "channel");
          setProblem(p);
          if (!p && v !== value) onCommit(v);
        }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      {problem && <div className="field-error" role="alert">{problem}</div>}
    </>
  );
}
