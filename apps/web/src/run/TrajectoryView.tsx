/** Trajectory (G-10): a conversation as a tree across the main agent, its helper agents, steps, frames and framework
 * parts, each row on its run's time bar. On the Runs page and in the editor's Timeline. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { ThreadTrajectory } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";
import { bar, rows, took } from "./trajectory";

const KIND: Record<string, string> = {
  run: "Run", group: "Frame", step: "Step", agent: "Helper", model: "Model", tool: "Tool", message: "Text",
  part: "Part", interrupt: "Waits", error: "Error", note: "Note",
};
const STATUS: Record<string, string> = { ok: "ok", success: "ok", error: "err", interrupted: "warn", running: "status-running", cancelled: "" };

export function TrajectoryView({ threadId, runSeq = 0, onPick }: {
  threadId: string; runSeq?: number; onPick?: (canvasNodeId: string) => void;
}) {
  const q = useQuery({
    queryKey: ["trajectory", threadId, runSeq],
    queryFn: () => apiFetch<ThreadTrajectory>(`/threads/${threadId}/trajectory`),
  });
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const flip = (id: string) => setToggled((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const runs = Array.isArray(q.data?.runs) ? q.data.runs : [];
  return (
    <div className="trajectory" data-testid="trajectory">
      <QueryStatus q={q} what="the trajectory" />
      {q.data && runs.length === 0 && <p className="muted small">This conversation has no runs yet.</p>}
      <ul role="tree" aria-label="trajectory">
        {rows(runs, toggled).map(({ node, depth, open, hasChildren, run }) => {
          const where = bar(node, run);
          const detailOpen = !hasChildren && node.detail && toggled.has(node.id);
          return (
            <li key={node.id} role="treeitem" aria-expanded={hasChildren ? open : undefined} className={`trajectory-row kind-${node.kind}`}>
              <div className="row gap" style={{ paddingLeft: depth * 16 }}>
                <button type="button" className="link trajectory-toggle" aria-label={`${open || detailOpen ? "close" : "open"} ${node.label}`}
                  disabled={!hasChildren && !node.detail} onClick={() => flip(node.id)}>
                  {hasChildren || node.detail ? (open || detailOpen ? "▾" : "▸") : "·"}
                </button>
                <span className="badge small">{KIND[node.kind] ?? node.kind}</span>
                {node.canvas_node_id && onPick
                  ? <button type="button" className="link trajectory-label" title="Show this step on the canvas"
                    onClick={() => onPick(node.canvas_node_id as string)}>{node.label}</button>
                  : <span className="trajectory-label" title={node.label}>{node.label}</span>}
                {node.status && <span className={`badge small ${STATUS[node.status] ?? ""}`}>{node.status}</span>}
                <span className="spacer" />
                {node.tokens ? <small className="muted">{node.tokens} tok</small> : null}
                <small className="muted">{took(node.duration_ms)}</small>
                <span className="trajectory-bar" aria-hidden>
                  {where && <span style={{ left: `${where.left}%`, width: `${where.width}%` }} />}
                </span>
              </div>
              {detailOpen && <pre className="mono small trajectory-detail">{JSON.stringify(node.detail, null, 2)}</pre>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
