/** Canonical run events (AG-UI based, spec doc 12 §C.3) as produced by the AgentCanvas runner. */
export interface InterruptInfo {
  id: string;
  kind: "tool_approval" | "approval" | "input";
  canvas_node_id: string | null;
  payload: unknown;
}

export type RunEvent = { seq?: number } & (
  | { type: "RUN_STARTED"; runId?: string; threadId: string; workflowId: string; mode?: string }
  | {
      type: "RUN_FINISHED"; runId?: string; threadId: string; outcome: "success" | "interrupt"; status?: string;
      breakpoint?: boolean; interrupts?: InterruptInfo[]; next?: string[]; output?: Record<string, unknown>;
    }
  | { type: "RUN_ERROR"; message: string; code?: string }
  | { type: "STEP_STARTED"; stepName: string; canvas_node_id: string }
  | { type: "STEP_FINISHED"; stepName: string; canvas_node_id: string; status: "ok" | "error" | "interrupted"; error?: string }
  | { type: "TEXT_MESSAGE_CONTENT"; canvas_node_id: string | null; delta: string }
  | { type: "STATE_DELTA"; canvas_node_id: string | null; delta: Record<string, unknown> }
  | { type: "CUSTOM"; name: string; value: Record<string, unknown> }
);
