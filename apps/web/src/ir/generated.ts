/* Generated from packages/ir-schema/ir.schema.json (pnpm gen:ir). Do not edit. */

export type IrVersion = "1.0" | "1.1";
export type Id = string;
export type Name = string;
export type Description = string;
export type Kind = "workflow" | "component";
export type Framework = string;
export type Name1 = string;
export type Reducer = "replace" | "append" | "add_messages" | "sum" | "merge";
export type Storage = "full" | "delta";
export type Io = "inout" | "in" | "out" | "private";
export type Description1 = string | null;
export type Channels = Channel[];
export type Name2 = string;
export type Required = boolean;
export type Source = "input" | "user" | "tenant";
export type Description2 = string | null;
export type Fields = ContextField[];
export type Id1 = string;
export type Type2 = string;
export type Name3 = string;
export type Label = string | null;
export type Resources = Resource[];
export type Id2 = string;
export type Type3 = string;
export type Name4 = string;
export type Label1 = string | null;
export type MaxAttempts = number;
export type InitialInterval = number;
export type BackoffFactor = number;
export type MaxInterval = number;
export type Jitter = boolean;
export type RetryOn = string[];
export type RunTimeout = number | null;
export type IdleTimeout = number | null;
export type RefreshOn = "auto" | "heartbeat";
export type Ttl = number | null;
export type ErrorRoute = string | null;
export type Trace = "full" | "omit_inputs" | "omit_outputs" | "omit";
export type Disabled = boolean;
export type Nodes = Node[];
export type Id3 = string;
export type Kind1 = "flow" | "wiring" | "data";
export type Node1 = string;
export type Port = string | null;
export type Order = number | null;
export type Label2 = string | null;
export type Edges = Edge[];
export type RecursionLimit = number;
export type Durability = "exit" | "async" | "sync";
export type InputChannels = string[] | null;
export type OutputChannels = string[] | null;
export type Experimental = boolean;

export interface Workflow {
  ir_version?: IrVersion;
  id: Id;
  name: Name;
  description?: Description;
  kind?: Kind;
  framework?: Framework;
  state?: StateSchema;
  context?: ContextSchema;
  resources?: Resources;
  nodes?: Nodes;
  edges?: Edges;
  settings?: WorkflowSettings;
  metadata?: Metadata1;
}
export interface StateSchema {
  channels?: Channels;
}
/**
 * One field of the shared graph state.
 */
export interface Channel {
  name: Name1;
  type?: Type;
  reducer?: Reducer;
  default?: Default;
  storage?: Storage;
  io?: Io;
  description?: Description1;
}
export interface Type {
  [k: string]: unknown;
}
export interface Default {
  [k: string]: unknown;
}
export interface ContextSchema {
  fields?: Fields;
}
/**
 * Run-scoped, immutable value (LangGraph ``Runtime.context``), e.g. user_id, tenant_id.
 *
 * ``source="user"`` means the runner fills it from the authenticated identity — never from
 * model output or client input (spec doc 08 rule 2).
 */
export interface ContextField {
  name: Name2;
  type?: Type1;
  required?: Required;
  source?: Source;
  description?: Description2;
}
export interface Type1 {
  [k: string]: unknown;
}
/**
 * Constructed once and injected into nodes by wiring edges (models, tools, middleware...).
 */
export interface Resource {
  id: Id1;
  type: Type2;
  name: Name3;
  label?: Label;
  config?: Config;
}
export interface Config {
  [k: string]: unknown;
}
/**
 * A runtime step in the graph.
 */
export interface Node {
  id: Id2;
  type: Type3;
  name: Name4;
  label?: Label1;
  config?: Config1;
  policies?: NodePolicies | null;
  disabled?: Disabled;
  metadata?: Metadata;
}
export interface Config1 {
  [k: string]: unknown;
}
export interface NodePolicies {
  retry?: RetryPolicySpec | null;
  timeout?: TimeoutPolicySpec | null;
  cache?: CachePolicySpec | null;
  error_route?: ErrorRoute;
  trace?: Trace;
}
export interface RetryPolicySpec {
  max_attempts?: MaxAttempts;
  initial_interval?: InitialInterval;
  backoff_factor?: BackoffFactor;
  max_interval?: MaxInterval;
  jitter?: Jitter;
  retry_on?: RetryOn;
}
export interface TimeoutPolicySpec {
  run_timeout?: RunTimeout;
  idle_timeout?: IdleTimeout;
  refresh_on?: RefreshOn;
}
export interface CachePolicySpec {
  ttl?: Ttl;
}
export interface Metadata {
  [k: string]: unknown;
}
export interface Edge {
  id: Id3;
  kind: Kind1;
  source: Endpoint;
  target: Endpoint;
  order?: Order;
  label?: Label2;
}
export interface Endpoint {
  node: Node1;
  port?: Port;
}
export interface WorkflowSettings {
  recursion_limit?: RecursionLimit;
  durability?: Durability;
  node_defaults?: NodePolicies | null;
  input_channels?: InputChannels;
  output_channels?: OutputChannels;
  experimental?: Experimental;
}
export interface Metadata1 {
  [k: string]: unknown;
}
