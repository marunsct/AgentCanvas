/** Node runtime policies (FR-NODE-07): retry, timeout, cache, error route, trace, bypass. */
import { useEditor } from "../editor/store";
import type { IRNode, NodePolicies } from "../ir/model";

const num = (v: string) => (v.trim() === "" ? null : Number(v));

export function PoliciesEditor({ node }: { node: IRNode }) {
  const wf = useEditor((s) => s.wf);
  const dispatch = useEditor((s) => s.dispatch);
  const p: NodePolicies = node.policies ?? {};
  const update = (patch: Record<string, unknown>) =>
    dispatch([{ op: "UpdateNode", id: node.id, patch: { policies: patch } }], { debounceMs: 400 });

  return (
    <fieldset className="policies">
      <legend>Runtime policies</legend>
      <label>
        Retries (max attempts)
        <input type="number" min={1} max={20} value={p.retry?.max_attempts ?? ""} placeholder="off"
          onChange={(e) => update({ retry: num(e.target.value) === null ? null : { max_attempts: num(e.target.value) } })} />
      </label>
      <label>
        Timeout (seconds)
        <input type="number" min={0} step="any" value={p.timeout?.run_timeout ?? ""} placeholder="none"
          onChange={(e) => update({ timeout: num(e.target.value) === null ? null : { run_timeout: num(e.target.value) } })} />
      </label>
      <label>
        Cache TTL (seconds)
        <input type="number" min={1} value={p.cache?.ttl ?? ""} placeholder="no cache"
          onChange={(e) => update({ cache: num(e.target.value) === null ? null : { ttl: num(e.target.value) } })} />
      </label>
      <label>
        On error, go to
        <select value={p.error_route ?? ""} onChange={(e) => update({ error_route: e.target.value || null })}>
          <option value="">— fail the run —</option>
          {wf?.nodes.filter((n) => n.id !== node.id).map((n) => <option key={n.id} value={n.id}>{n.label || n.name}</option>)}
        </select>
      </label>
      <label>
        Tracing
        <select value={p.trace ?? "full"} onChange={(e) => update({ trace: e.target.value })}>
          <option value="full">full inputs & outputs</option>
          <option value="summarize">summarize (shapes, no content)</option>
          <option value="omit_inputs">omit inputs</option>
          <option value="omit_outputs">omit outputs</option>
          <option value="omit">omit both</option>
        </select>
      </label>
      <label className="row">
        <input type="checkbox" checked={Boolean(node.disabled)}
          onChange={(e) => dispatch([{ op: "UpdateNode", id: node.id, patch: { disabled: e.target.checked } }])} />
        Bypass this node (passes state through)
      </label>
    </fieldset>
  );
}
