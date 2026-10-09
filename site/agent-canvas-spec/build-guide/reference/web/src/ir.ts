// TypeScript mirror of agentcanvas/ir/models.py. In the real project generate this
// from the Pydantic JSON Schema (see build guide M1-T4) instead of writing it by hand.
export type Reducer = "replace" | "append" | "add_messages" | "sum" | "merge";
export interface Channel { name: string; type: Record<string, unknown>; reducer: Reducer; default?: unknown }
export interface Resource { id: string; type: "model.chat" | "tool.python"; name: string; config: Record<string, unknown> }
export interface IRNode {
  id: string;
  type: "core.llm" | "core.agent" | "core.router" | "core.approval" | "core.set" | "core.script";
  name: string;
  label?: string | null;
  config: Record<string, unknown>;
}
export interface Endpoint { node: string; port?: string | null }
export interface IREdge { id: string; kind: "flow" | "wiring"; source: Endpoint; target: Endpoint }
export interface Workflow {
  ir_version: "1.0"; id: string; name: string;
  state: { channels: Channel[] }; resources: Resource[]; nodes: IRNode[]; edges: IREdge[];
}

// Canonical run events (AG-UI based) produced by the runner
export type RunEvent =
  | { type: "RUN_STARTED"; threadId: string; workflowId: string }
  | { type: "RUN_FINISHED"; threadId: string; outcome: "success" | "interrupt" }
  | { type: "RUN_ERROR"; message: string }
  | { type: "STEP_STARTED"; stepName: string; canvas_node_id: string }
  | { type: "STEP_FINISHED"; stepName: string; canvas_node_id: string; status: "ok" | "error" | "interrupted" }
  | { type: "TEXT_MESSAGE_CONTENT"; canvas_node_id: string | null; delta: string }
  | { type: "STATE_DELTA"; canvas_node_id: string | null; delta: Record<string, unknown> }
  | { type: "CUSTOM"; name: string; value: { id?: string; payload?: unknown } & Record<string, unknown> };
