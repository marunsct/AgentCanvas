/* Generated from packages/ir-schema/openapi.json (pnpm gen:api). Do not edit. */

export type Index = number | null;
export type Disabled = boolean;
export type Id = string;
export type Label = string | null;
export type Name = string;
export type Ttl = number | null;
export type ErrorRoute = string | null;
export type BackoffFactor = number;
export type InitialInterval = number;
export type Jitter = boolean;
export type MaxAttempts = number;
export type MaxInterval = number;
export type RetryOn = string[];
export type IdleTimeout = number | null;
export type RefreshOn = "auto" | "heartbeat";
export type RunTimeout = number | null;
export type Trace = "full" | "omit_inputs" | "omit_outputs" | "omit";
export type Type = string;
export type Op = "AddNode";
export type Index1 = number | null;
export type Op1 = "AddResource";
export type Id1 = string;
export type Label1 = string | null;
export type Name1 = string;
export type Type1 = string;
export type After = string[];
export type Before = string[];
export type Description = string | null;
export type Io = "inout" | "in" | "out" | "private";
export type Name2 = string;
export type Reducer = "replace" | "append" | "add_messages" | "sum" | "merge";
export type Storage = "full" | "delta";
/**
 * @minItems 1
 */
export type Commands = [
  (
    | AddNode
    | RemoveNode
    | UpdateNode
    | ReplaceNode
    | RenameNode
    | AddResource
    | RemoveResource
    | UpdateResource
    | ReplaceResource
    | Connect
    | Disconnect
    | SetChannels
    | SetContext
    | UpdateSettings
    | ReplaceSettings
    | UpdateMeta
  ),
  ...(
    | AddNode
    | RemoveNode
    | UpdateNode
    | ReplaceNode
    | RenameNode
    | AddResource
    | RemoveResource
    | UpdateResource
    | ReplaceResource
    | Connect
    | Disconnect
    | SetChannels
    | SetContext
    | UpdateSettings
    | ReplaceSettings
    | UpdateMeta
  )[]
];
export type Id2 = string;
export type Op2 = "RemoveNode";
export type Id3 = string;
export type Op3 = "UpdateNode";
export type Op4 = "ReplaceNode";
export type Id4 = string;
export type Name3 = string;
export type Op5 = "RenameNode";
export type Id5 = string;
export type Op6 = "RemoveResource";
export type Id6 = string;
export type Op7 = "UpdateResource";
export type Op8 = "ReplaceResource";
export type Id7 = string;
export type Kind = "flow" | "wiring" | "data";
export type Label2 = string | null;
export type Order = number | null;
export type Node1 = string;
export type Port = string | null;
export type Index2 = number | null;
export type Op9 = "Connect";
export type Id8 = string;
export type Op10 = "Disconnect";
export type Channels = Channel[];
export type Op11 = "SetChannels";
export type Description1 = string | null;
export type Name4 = string;
export type Required = boolean;
export type Source = "input" | "user" | "tenant";
export type Fields = ContextField[];
export type Op12 = "SetContext";
export type Op13 = "UpdateSettings";
export type Op14 = "ReplaceSettings";
export type Description2 = string | null;
export type Metadata1 = {
  [k: string]: unknown;
} | null;
export type Name5 = string | null;
export type Op15 = "UpdateMeta";
export type CreatedAt = string;
export type Code = string;
export type EdgeId = string | null;
export type Hint = string | null;
export type Message = string;
export type NodeId = string | null;
export type Path = string | null;
export type Severity = "error" | "warning" | "info";
export type Diagnostics = Diagnostic[];
export type Id9 = string;
export type Inverse = (
  | AddNode
  | RemoveNode
  | UpdateNode
  | ReplaceNode
  | RenameNode
  | AddResource
  | RemoveResource
  | UpdateResource
  | ReplaceResource
  | Connect
  | Disconnect
  | SetChannels
  | SetContext
  | UpdateSettings
  | ReplaceSettings
  | UpdateMeta
)[];
export type IrHash = string;
export type Name6 = string;
export type ProjectId = string;
export type UpdatedAt = string;
export type Version = number;
export type Name7 = string;
export type Template = string | null;
export type Kind1 = "cel" | "template";
export type Source1 = string;
export type Error = string | null;
export type Ok = boolean;
export type AsNode = string | null;
export type CheckpointId = string;
export type Mode = "branch" | "copy";
export type Stream = boolean;
export type Values = {
  [k: string]: unknown;
} | null;
export type Location = (string | number)[];
export type Message1 = string;
export type ErrorType = string;
export type Detail = ValidationError[];
export type CanvasNodeId = string;
export type CreatedAt1 = string;
export type DecidedAt = string | null;
export type Decision1 = {
  [k: string]: unknown;
} | null;
export type Id10 = string;
export type Kind2 = string;
export type RunId = string;
export type Status = string;
export type ThreadId = string;
export type WorkflowId = string | null;
export type WorkflowName = string | null;
export type Replace = boolean;
export type ContextWindow = number | null;
export type Id11 = string;
export type Label3 = string;
export type PriceInPerMtok = number | null;
export type PriceOutPerMtok = number | null;
export type Provider = string;
export type Reasoning = boolean;
export type StructuredOutput = boolean;
export type Tools = boolean;
export type Vision = boolean;
export type Category = string;
export type ChannelFields = string[];
export type ChannelMapFields = string[];
export type Color = string | null;
export type Description3 = string;
export type Docs = string | null;
export type DynamicOutputs = "routes" | null;
export type HostedForbidden = boolean;
export type Icon = string;
export type Description4 = string | null;
export type Multi = boolean;
export type Name8 = string;
export type Ordered = boolean;
export type Required1 = boolean;
export type Type4 = string;
export type Inputs = PortSpec[];
export type Interrupts = boolean;
export type Kind3 = "runtime" | "resource" | "middleware" | "trigger";
export type Label4 = string;
export type ListWriteFields = string[];
export type Outputs = PortSpec[];
export type ReadFields = string[];
export type Reads = string[];
export type SideEffects = ("external_write" | "message_send" | "code_exec" | "spend" | "pii_read" | "host_exec")[];
export type Stability = "stable" | "beta" | "experimental";
export type TemplateFields = string[];
export type Type5 = string;
export type Version1 = string;
export type Writes = string[];
export type CreatedAt2 = string;
export type Id12 = string;
export type Name9 = string;
export type InterruptId = string | null;
export type Stream1 = boolean;
export type EndedAt = string | null;
export type Error1 = string | null;
export type Id13 = string;
export type IrHash1 = string;
export type Mode1 = string;
export type StartedAt = string;
export type Status1 = string;
export type ThreadId1 = string;
export type Durability = ("exit" | "async" | "sync") | null;
export type Mode2 = "normal" | "debug";
export type Stream2 = boolean;
export type ThreadId2 = string | null;
export type AsNode1 = string | null;
export type CheckpointId1 = string | null;
export type Description5 = string;
export type Id14 = string;
export type Name10 = string;
export type NodeCount = number;
export type CreatedAt3 = string;
export type Id15 = string;
export type Status2 = string;
export type Title = string | null;
export type UpdatedAt1 = string;
export type WorkflowId1 = string;
export type Items = ThreadOut[];
export type NextCursor = string | null;
export type CreatedAt4 = string;
export type Diagnostics1 = Diagnostic[];
export type Id16 = string;
export type IrHash2 = string;
export type Name11 = string;
export type ProjectId1 = string;
export type UpdatedAt2 = string;
export type Version2 = number;
export type Id17 = string;
export type Name12 = string;
export type UpdatedAt3 = string;
export type Version3 = number;
export type Items1 = WorkflowSummary[];
export type NextCursor1 = string | null;

export interface ApiSchemas {
  AddNode?: AddNode;
  AddResource?: AddResource;
  Breakpoints?: Breakpoints;
  CachePolicySpec?: CachePolicySpec;
  Channel?: Channel;
  CommandBatch?: CommandBatch;
  CommandResult?: CommandResult;
  Connect?: Connect;
  ContextField?: ContextField;
  CreateWorkflow?: CreateWorkflow;
  Decision?: Decision;
  Diagnostic?: Diagnostic;
  Disconnect?: Disconnect;
  Edge?: Edge;
  Endpoint?: Endpoint;
  ExpressionCheck?: ExpressionCheck;
  ExpressionResult?: ExpressionResult;
  ForkRequest?: ForkRequest;
  HTTPValidationError?: HTTPValidationError;
  InterruptOut?: InterruptOut;
  LayoutUpdate?: LayoutUpdate;
  ModelInfo?: ModelInfo;
  Node?: Node;
  NodeManifest?: NodeManifest;
  NodePolicies?: NodePolicies;
  PortSpec?: PortSpec;
  ProjectOut?: ProjectOut;
  RemoveNode?: RemoveNode;
  RemoveResource?: RemoveResource;
  RenameNode?: RenameNode;
  ReplaceIR?: ReplaceIR;
  ReplaceNode?: ReplaceNode;
  ReplaceResource?: ReplaceResource;
  ReplaceSettings?: ReplaceSettings;
  Resource?: Resource;
  ResumeRequest?: ResumeRequest;
  RetryPolicySpec?: RetryPolicySpec;
  RunOut?: RunOut;
  RunRequest?: RunRequest;
  SetChannels?: SetChannels;
  SetContext?: SetContext;
  StateEdit?: StateEdit;
  TemplateInfo?: TemplateInfo;
  ThreadOut?: ThreadOut;
  ThreadPage?: ThreadPage;
  TimeoutPolicySpec?: TimeoutPolicySpec;
  UpdateMeta?: UpdateMeta;
  UpdateNode?: UpdateNode;
  UpdateResource?: UpdateResource;
  UpdateSettings?: UpdateSettings;
  ValidationError?: ValidationError;
  WorkflowOut?: WorkflowOut;
  WorkflowPage?: WorkflowPage;
  WorkflowSummary?: WorkflowSummary;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AddNode".
 */
export interface AddNode {
  index?: Index;
  node: Node;
  op?: Op;
}
/**
 * A runtime step in the graph.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Node".
 */
export interface Node {
  config?: Config;
  disabled?: Disabled;
  id: Id;
  label?: Label;
  metadata?: Metadata;
  name: Name;
  policies?: NodePolicies | null;
  type: Type;
}
export interface Config {
  [k: string]: unknown;
}
export interface Metadata {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NodePolicies".
 */
export interface NodePolicies {
  cache?: CachePolicySpec | null;
  error_route?: ErrorRoute;
  retry?: RetryPolicySpec | null;
  timeout?: TimeoutPolicySpec | null;
  trace?: Trace;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CachePolicySpec".
 */
export interface CachePolicySpec {
  ttl?: Ttl;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RetryPolicySpec".
 */
export interface RetryPolicySpec {
  backoff_factor?: BackoffFactor;
  initial_interval?: InitialInterval;
  jitter?: Jitter;
  max_attempts?: MaxAttempts;
  max_interval?: MaxInterval;
  retry_on?: RetryOn;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TimeoutPolicySpec".
 */
export interface TimeoutPolicySpec {
  idle_timeout?: IdleTimeout;
  refresh_on?: RefreshOn;
  run_timeout?: RunTimeout;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AddResource".
 */
export interface AddResource {
  index?: Index1;
  op?: Op1;
  resource: Resource;
}
/**
 * Constructed once and injected into nodes by wiring edges (models, tools, middleware...).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Resource".
 */
export interface Resource {
  config?: Config1;
  id: Id1;
  label?: Label1;
  name: Name1;
  type: Type1;
}
export interface Config1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Breakpoints".
 */
export interface Breakpoints {
  after?: After;
  before?: Before;
}
/**
 * One field of the shared graph state.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Channel".
 */
export interface Channel {
  default?: Default;
  description?: Description;
  io?: Io;
  name: Name2;
  reducer?: Reducer;
  storage?: Storage;
  type?: Type2;
}
export interface Default {
  [k: string]: unknown;
}
export interface Type2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CommandBatch".
 */
export interface CommandBatch {
  commands: Commands;
}
/**
 * Removes a node and every edge touching it.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RemoveNode".
 */
export interface RemoveNode {
  id: Id2;
  op?: Op2;
}
/**
 * JSON merge patch on a node (label, config, policies, disabled, metadata, name).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateNode".
 */
export interface UpdateNode {
  id: Id3;
  op?: Op3;
  patch: Patch;
}
export interface Patch {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ReplaceNode".
 */
export interface ReplaceNode {
  node: Node;
  op?: Op4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RenameNode".
 */
export interface RenameNode {
  id: Id4;
  name: Name3;
  op?: Op5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RemoveResource".
 */
export interface RemoveResource {
  id: Id5;
  op?: Op6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateResource".
 */
export interface UpdateResource {
  id: Id6;
  op?: Op7;
  patch: Patch1;
}
export interface Patch1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ReplaceResource".
 */
export interface ReplaceResource {
  op?: Op8;
  resource: Resource;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Connect".
 */
export interface Connect {
  edge: Edge;
  index?: Index2;
  op?: Op9;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Edge".
 */
export interface Edge {
  id: Id7;
  kind: Kind;
  label?: Label2;
  order?: Order;
  source: Endpoint;
  target: Endpoint;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Endpoint".
 */
export interface Endpoint {
  node: Node1;
  port?: Port;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Disconnect".
 */
export interface Disconnect {
  id: Id8;
  op?: Op10;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SetChannels".
 */
export interface SetChannels {
  channels: Channels;
  op?: Op11;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SetContext".
 */
export interface SetContext {
  fields: Fields;
  op?: Op12;
}
/**
 * Run-scoped, immutable value (LangGraph ``Runtime.context``), e.g. user_id, tenant_id.
 *
 * ``source="user"`` means the runner fills it from the authenticated identity — never from
 * model output or client input (spec doc 08 rule 2).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ContextField".
 */
export interface ContextField {
  description?: Description1;
  name: Name4;
  required?: Required;
  source?: Source;
  type?: Type3;
}
export interface Type3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateSettings".
 */
export interface UpdateSettings {
  op?: Op13;
  patch: Patch2;
}
export interface Patch2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ReplaceSettings".
 */
export interface ReplaceSettings {
  op?: Op14;
  settings: Settings;
}
export interface Settings {
  [k: string]: unknown;
}
/**
 * Change name / description / metadata of the workflow.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateMeta".
 */
export interface UpdateMeta {
  description?: Description2;
  metadata?: Metadata1;
  name?: Name5;
  op?: Op15;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CommandResult".
 */
export interface CommandResult {
  created_at: CreatedAt;
  diagnostics: Diagnostics;
  id: Id9;
  inverse: Inverse;
  ir: Ir;
  ir_hash: IrHash;
  layout: Layout;
  name: Name6;
  project_id: ProjectId;
  updated_at: UpdatedAt;
  version: Version;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Diagnostic".
 */
export interface Diagnostic {
  code: Code;
  edge_id?: EdgeId;
  hint?: Hint;
  message: Message;
  node_id?: NodeId;
  path?: Path;
  severity: Severity;
}
export interface Ir {
  [k: string]: unknown;
}
export interface Layout {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CreateWorkflow".
 */
export interface CreateWorkflow {
  name?: Name7;
  template?: Template;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Decision".
 */
export interface Decision {
  value: Value;
}
export interface Value {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionCheck".
 */
export interface ExpressionCheck {
  kind: Kind1;
  source: Source1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionResult".
 */
export interface ExpressionResult {
  error?: Error;
  ok: Ok;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ForkRequest".
 */
export interface ForkRequest {
  as_node?: AsNode;
  checkpoint_id: CheckpointId;
  mode?: Mode;
  stream?: Stream;
  values?: Values;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "HTTPValidationError".
 */
export interface HTTPValidationError {
  detail?: Detail;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ValidationError".
 */
export interface ValidationError {
  ctx?: Context;
  input?: Input;
  loc: Location;
  msg: Message1;
  type: ErrorType;
}
export interface Context {}
export interface Input {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "InterruptOut".
 */
export interface InterruptOut {
  canvas_node_id: CanvasNodeId;
  created_at: CreatedAt1;
  decided_at: DecidedAt;
  decision: Decision1;
  id: Id10;
  kind: Kind2;
  payload: Payload;
  run_id: RunId;
  status: Status;
  thread_id: ThreadId;
  workflow_id?: WorkflowId;
  workflow_name?: WorkflowName;
}
export interface Payload {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "LayoutUpdate".
 */
export interface LayoutUpdate {
  positions: Positions;
  replace?: Replace;
}
export interface Positions {
  [k: string]: {
    [k: string]: number;
  };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelInfo".
 */
export interface ModelInfo {
  context_window?: ContextWindow;
  id: Id11;
  label: Label3;
  price_in_per_mtok?: PriceInPerMtok;
  price_out_per_mtok?: PriceOutPerMtok;
  provider: Provider;
  reasoning?: Reasoning;
  structured_output?: StructuredOutput;
  tools?: Tools;
  vision?: Vision;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NodeManifest".
 */
export interface NodeManifest {
  category: Category;
  channel_fields?: ChannelFields;
  channel_map_fields?: ChannelMapFields;
  color?: Color;
  config_schema?: ConfigSchema;
  description?: Description3;
  docs?: Docs;
  dynamic_outputs?: DynamicOutputs;
  hosted_forbidden?: HostedForbidden;
  icon?: Icon;
  inputs?: Inputs;
  interrupts?: Interrupts;
  kind: Kind3;
  label: Label4;
  list_write_fields?: ListWriteFields;
  outputs?: Outputs;
  read_fields?: ReadFields;
  reads?: Reads;
  side_effects?: SideEffects;
  stability?: Stability;
  targets?: Targets;
  template_fields?: TemplateFields;
  type: Type5;
  version?: Version1;
  writes?: Writes;
}
export interface ConfigSchema {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PortSpec".
 */
export interface PortSpec {
  description?: Description4;
  multi?: Multi;
  name: Name8;
  ordered?: Ordered;
  required?: Required1;
  type: Type4;
}
export interface Targets {
  [k: string]: "full" | "partial" | "none";
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectOut".
 */
export interface ProjectOut {
  created_at: CreatedAt2;
  id: Id12;
  name: Name9;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ReplaceIR".
 */
export interface ReplaceIR {
  ir: Ir1;
}
export interface Ir1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ResumeRequest".
 */
export interface ResumeRequest {
  interrupt_id?: InterruptId;
  stream?: Stream1;
  value?: Value1;
}
export interface Value1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RunOut".
 */
export interface RunOut {
  ended_at: EndedAt;
  error: Error1;
  id: Id13;
  ir_hash: IrHash1;
  mode: Mode1;
  started_at: StartedAt;
  status: Status1;
  thread_id: ThreadId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RunRequest".
 */
export interface RunRequest {
  breakpoints?: Breakpoints | null;
  context?: Context1;
  durability?: Durability;
  input?: Input1;
  mode?: Mode2;
  stream?: Stream2;
  thread_id?: ThreadId2;
}
export interface Context1 {
  [k: string]: unknown;
}
export interface Input1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "StateEdit".
 */
export interface StateEdit {
  as_node?: AsNode1;
  checkpoint_id?: CheckpointId1;
  values: Values1;
}
export interface Values1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  description: Description5;
  id: Id14;
  name: Name10;
  node_count: NodeCount;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadOut".
 */
export interface ThreadOut {
  created_at: CreatedAt3;
  id: Id15;
  status: Status2;
  title: Title;
  updated_at: UpdatedAt1;
  workflow_id: WorkflowId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadPage".
 */
export interface ThreadPage {
  items: Items;
  next_cursor: NextCursor;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowOut".
 */
export interface WorkflowOut {
  created_at: CreatedAt4;
  diagnostics: Diagnostics1;
  id: Id16;
  ir: Ir2;
  ir_hash: IrHash2;
  layout: Layout1;
  name: Name11;
  project_id: ProjectId1;
  updated_at: UpdatedAt2;
  version: Version2;
}
export interface Ir2 {
  [k: string]: unknown;
}
export interface Layout1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowPage".
 */
export interface WorkflowPage {
  items: Items1;
  next_cursor: NextCursor1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowSummary".
 */
export interface WorkflowSummary {
  id: Id17;
  name: Name12;
  updated_at: UpdatedAt3;
  version: Version3;
}
