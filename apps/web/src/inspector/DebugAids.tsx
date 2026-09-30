/** Pinned output (nodes) and mock replies (chat models) for debug runs (M4-T9). Kept per viewer, never exported. */
import { useState } from "react";
import { useEditor } from "../editor/store";
import type { IRNode, IRResource } from "../ir/model";
import { useRun } from "../run/runStore";

const UNPINNABLE = new Set(["core.router", "core.map"]);

export function PinPanel({ node }: { node: IRNode }) {
  const pinned = useEditor((s) => s.pins[node.id]);
  const last = useRun((s) => s.deltas[node.id]);
  const setPin = useEditor((s) => s.setPin);
  if (UNPINNABLE.has(node.type)) return null;
  return (
    <div className="debug-aid" aria-label="pinned output">
      {pinned ? (
        <>
          <h4>📌 Pinned output <small className="muted">debug runs replay it instead of running this node</small></h4>
          <pre className="json">{JSON.stringify(pinned, null, 2).slice(0, 1500)}</pre>
          <button type="button" onClick={() => setPin(node.id, null)}>Unpin</button>
        </>
      ) : last ? (
        <button type="button" onClick={() => setPin(node.id, last)} title="Debug runs will skip this node and replay this output">
          📌 Pin last output
        </button>
      ) : null}
    </div>
  );
}

/** One reply per line; a line starting with `{` is JSON, e.g. {"tool_calls": [{"name": "search", "args": {"q": "x"}}]}. */
export function parseReplies(text: string): unknown[] {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    if (!l.startsWith("{")) return l;
    try {
      return JSON.parse(l) as unknown;
    } catch {
      return l;
    }
  });
}

export function MockPanel({ resource }: { resource: IRResource }) {
  const replies = useEditor((s) => s.mocks[resource.name]);
  const setMock = useEditor((s) => s.setMock);
  const [draft, setDraft] = useState(() => (replies ?? []).map((r) => (typeof r === "string" ? r : JSON.stringify(r))).join("\n"));
  return (
    <div className="debug-aid" aria-label="mock model">
      <label>
        Mock replies for debug runs {replies?.length ? <span className="badge warn">on</span> : null}
        <textarea className="mono" rows={3} value={draft} aria-label="mock replies" spellCheck={false}
          placeholder={'one reply per line, e.g. low\n{"tool_calls": [{"name": "send_reply", "args": {"text": "Hi"}}]}'}
          onChange={(e) => setDraft(e.target.value)} onBlur={() => setMock(resource.name, parseReplies(draft))} />
      </label>
      <small className="muted">Debug runs answer with these instead of calling the model (turn n gets line n; the last line repeats).</small>
    </div>
  );
}
