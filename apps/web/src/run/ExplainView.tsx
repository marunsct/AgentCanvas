/** Explain mode (FR-X-17): a readable narrative of each super-step, including why routers chose a branch. */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { apiFetch } from "../api/client";
import { useRun } from "./runStore";

interface ExplainStep {
  step: number;
  checkpoint_id: string;
  parallel: boolean;
  text: string;
  ran: { node_id: string; name: string; label: string; completed: boolean; wrote: Record<string, string>;
    decision?: { route: string | null; reason: string; target_name?: string } }[];
}

export function ExplainView() {
  const threadId = useRun((s) => s.threadId);
  const runSeq = useRun((s) => s.runSeq);
  const setHighlight = useRun((s) => s.setHighlight);
  const steps = useQuery({
    queryKey: ["explain", threadId, runSeq],
    queryFn: () => apiFetch<ExplainStep[]>(`/threads/${threadId}/explain`),
    enabled: Boolean(threadId),
  });
  useEffect(() => () => setHighlight([]), [setHighlight]);
  if (!threadId) return <p className="muted pad">Run the workflow, then read how it executed, step by step.</p>;
  return (
    <ol className="explain" data-testid="explain">
      {steps.data?.map((s) => (
        <li key={s.checkpoint_id} onMouseEnter={() => setHighlight(s.ran.map((r) => r.node_id))}
          onMouseLeave={() => setHighlight([])}>
          {s.parallel && <span className="badge">parallel</span>} {s.text}
          {s.ran.some((r) => Object.keys(r.wrote).length > 0) && (
            <ul>
              {s.ran.flatMap((r) => Object.entries(r.wrote).map(([k, v]) => (
                <li key={`${r.node_id}-${k}`}><code>{r.name}</code> → <code>{k}</code> = <span className="mono">{v}</span></li>
              )))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}
