/** State designer (FR-STATE-01, M2-T8): channels with type, reducer, storage and visibility. */
import { useEditor } from "../editor/store";
import type { Channel } from "../ir/model";

const TYPES = ["string", "number", "integer", "boolean", "array", "object"];
const REDUCERS = ["replace", "append", "add_messages", "sum", "merge"] as const;
const IO = ["inout", "in", "out", "private"] as const;
const NAME = /^[a-z][a-z0-9_]{0,62}$/;
const NO_CHANNELS: Channel[] = [];

export function StateDesigner() {
  const channels = useEditor((s) => s.wf?.state.channels ?? NO_CHANNELS);
  const dispatch = useEditor((s) => s.dispatch);
  const commit = (next: Channel[], debounceMs = 0) => dispatch([{ op: "SetChannels", channels: next }], { debounceMs });
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
              <td><ChannelName value={c.name} onCommit={(name) => edit(i, { name })} /></td>
              <td>
                <select aria-label={`${c.name} type`} value={(c.type?.type as string) ?? "string"} onChange={(e) => edit(i, { type: { type: e.target.value } })}>
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

function ChannelName({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  return (
    <input className="mono" defaultValue={value} aria-label="channel name"
      onBlur={(e) => { if (e.target.value !== value && NAME.test(e.target.value)) onCommit(e.target.value); }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
  );
}
