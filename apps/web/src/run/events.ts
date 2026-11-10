/** Canonical run events (AG-UI based, spec doc 12 §C.3) as produced by the AgentCanvas runner. */
export interface InterruptInfo {
  id: string;
  kind: "tool_approval" | "approval" | "input" | "timer"; // timer: a Wait step, continued by the server
  canvas_node_id: string | null;
  payload: unknown;
}

/** Tokens and cost of a run (RUN_FINISHED / RUN_ERROR) or of one node (accumulated from `ac.usage`). */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  priced: boolean; // false when a model had no catalog price (the cost is then a lower bound)
  cache_hits?: number; // S26: model answers taken from the answer cache
  fallbacks?: number; // S26: model calls a backup model answered
}

export type RunEvent = { seq?: number } & (
  | { type: "RUN_STARTED"; runId?: string; threadId: string; workflowId: string; mode?: string }
  | {
      type: "RUN_FINISHED"; runId?: string; threadId: string; outcome: "success" | "interrupt"; status?: string;
      breakpoint?: boolean; interrupts?: InterruptInfo[]; next?: string[]; output?: Record<string, unknown>;
      duration_ms?: number; usage?: Usage; traced?: boolean;
    }
  | { type: "RUN_ERROR"; message: string; code?: string; usage?: Usage }
  | { type: "STEP_STARTED"; stepName: string; canvas_node_id: string }
  | {
      type: "STEP_FINISHED"; stepName: string; canvas_node_id: string; status: "ok" | "error" | "interrupted"; error?: string;
      duration_ms?: number;
    }
  | { type: "TEXT_MESSAGE_CONTENT"; canvas_node_id: string | null; delta: string }
  | { type: "STATE_DELTA"; canvas_node_id: string | null; delta: Record<string, unknown> }
  | { type: "CUSTOM"; name: string; value: Record<string, unknown>; canvas_node_id?: string | null }
);

/** One rubric grading round of an agent (FR-EVAL-08, `ac.rubric`): `result` is set when the round ended. */
export interface RubricRound {
  node: string | null;
  iteration: number;
  result: string | null;
  explanation: string;
  gaps: string[];
}
