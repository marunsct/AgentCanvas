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
export type Trace = "full" | "summarize" | "omit_inputs" | "omit_outputs" | "omit";
export type Type = string;
export type Op = "AddNode";
export type Index1 = number | null;
export type Op1 = "AddResource";
export type Id1 = string;
export type Label1 = string | null;
export type Name1 = string;
export type Type1 = string;
export type Error = string | null;
export type Interrupts = {
  [k: string]: unknown;
}[];
export type Output = {
  [k: string]: unknown;
} | null;
export type RunId = string;
export type Status = "success" | "interrupted" | "error" | "cancelled" | "running";
export type ThreadId = string;
export type InterruptId = string | null;
/**
 * continue a conversation this deployment started
 */
export type ThreadId1 = string | null;
/**
 * your end user's id (context fields with source 'user')
 */
export type UserId = string | null;
export type Audience = string | null;
export type ClientId = string | null;
export type Issuer = string | null;
export type Mode = "dev" | "oidc";
export type Scopes = string | null;
export type Effect = string;
export type Gated = boolean;
export type Source = string;
export type Capabilities = Capability[];
export type Name2 = string;
export type NodeId = string;
export type Nodes = NodeBlast[];
export type Ungated = string[];
export type After = string[];
export type Before = string[];
export type DurationMs = number;
export type Failures = string[];
export type Id2 = string;
export type Name3 = string;
export type Passed = boolean;
export type Route = string[];
export type RunId1 = string | null;
export type Status1 = string | null;
export type ThreadId2 = string | null;
export type Handlers = {
  [k: string]: unknown;
}[];
export type Nodes1 = string[];
export type Problems = {
  [k: string]: string;
}[];
export type Schemas = string[];
export type Scripts = {
  [k: string]: unknown;
}[];
export type Tools = {
  [k: string]: unknown;
}[];
export type Description = string | null;
export type Io = "inout" | "in" | "out" | "private";
export type Name4 = string;
export type Reducer = "replace" | "append" | "add_messages" | "sum" | "merge";
export type Storage = "full" | "delta";
export type Detail = string;
export type Items = string[];
export type Name5 = string;
export type Ok = boolean;
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
export type Id3 = string;
export type Op2 = "RemoveNode";
export type Id4 = string;
export type Op3 = "UpdateNode";
export type Op4 = "ReplaceNode";
export type Id5 = string;
export type Name6 = string;
export type Op5 = "RenameNode";
export type Id6 = string;
export type Op6 = "RemoveResource";
export type Id7 = string;
export type Op7 = "UpdateResource";
export type Op8 = "ReplaceResource";
export type Id8 = string;
export type Kind = "flow" | "wiring" | "data";
export type Label2 = string | null;
export type Order = number | null;
export type Node1 = string;
export type Port = string | null;
export type Index2 = number | null;
export type Op9 = "Connect";
export type Id9 = string;
export type Op10 = "Disconnect";
export type Channels = Channel[];
export type Op11 = "SetChannels";
export type Description1 = string | null;
export type Name7 = string;
export type Required = boolean;
export type Source1 = "input" | "user" | "tenant";
export type Fields = ContextField[];
export type Op12 = "SetContext";
export type Op13 = "UpdateSettings";
export type Op14 = "ReplaceSettings";
export type Description2 = string | null;
export type Metadata1 = {
  [k: string]: unknown;
} | null;
export type Name8 = string | null;
export type Op15 = "UpdateMeta";
export type CreatedAt = string;
export type Code = string;
export type EdgeId = string | null;
export type Hint = string | null;
export type Message = string;
export type NodeId1 = string | null;
export type Path = string | null;
export type Severity = "error" | "warning" | "info";
export type Diagnostics = Diagnostic[];
export type Id10 = string;
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
export type Name9 = string;
export type ProjectId = string;
export type UpdatedAt = string;
export type Version = number;
export type Field = string;
export type Message1 = string;
export type Severity1 = "breaking" | "compatible";
export type Where = "input" | "output" | "context" | "pauses";
export type RequiredContext = string[];
export type Detail1 = string;
export type Kind1 = string;
export type Surfaces = Surface[];
export type Name10 = string;
export type Template = string | null;
export type DryRun = boolean;
export type Mocks = {
  [k: string]: unknown[];
} | null;
export type Pins = {
  [k: string]: {
    [k: string]: unknown;
  };
} | null;
export type Active = boolean;
export type CreatedAt1 = string;
export type Endpoint1 = string;
export type Environment = string;
export type Id11 = string;
export type KeyPrefix = string | null;
export type Semver = string;
export type VersionId = string;
export type A = string;
export type B = string;
export type Added = string[];
export type Path1 = string;
export type Fields1 = FieldChange[];
export type Id12 = string;
export type Name11 = string | null;
export type Changed = ElementChange[];
export type Removed = string[];
export type Summary1 = string;
export type Workflow = FieldChange[];
export type Enabled = boolean;
export type Type4 = string;
export type CreatedAt2 = string;
export type Id13 = string;
export type Results = {
  [k: string]: unknown;
}[];
export type Source2 = string;
export type Status2 = string;
export type Type5 = string;
/**
 * node ids that must run (and no others)
 */
export type Route1 = string[] | null;
export type Status3 = "success" | "interrupted" | "error";
export type Kind2 = "cel" | "template";
export type Source3 = string;
export type Error1 = string | null;
export type Ok1 = boolean;
export type Content = string;
export type Code1 = string;
export type Column = number;
export type EndColumn = number;
export type EndLine = number;
export type Line = number;
export type Message2 = string;
export type Diagnostics1 = LintItem[];
export type Path2 = string;
export type Modified = number;
export type Path3 = string;
export type Size = number;
export type Content1 = string;
export type AsNode = string | null;
export type CheckpointId = string;
export type Mode1 = "branch" | "copy";
export type Stream = boolean;
export type Values = {
  [k: string]: unknown;
} | null;
export type Name12 = string;
export type Location = (string | number)[];
export type Message3 = string;
export type ErrorType = string;
export type Detail2 = ValidationError[];
export type NodeId2 = string;
export type CanvasNodeId = string;
export type CreatedAt3 = string;
export type DecidedAt = string | null;
export type Decision1 = {
  [k: string]: unknown;
} | null;
export type Id14 = string;
export type Kind3 = string;
export type RunId2 = string;
export type Status4 = string;
export type ThreadId3 = string;
export type WorkflowId = string | null;
export type WorkflowName = string | null;
export type ApiKey = string;
export type Replace = boolean;
/**
 * Resource name (lets dev/test hosts substitute an in-process server)
 */
export type Name13 = string;
export type Description3 = string;
export type Destructive = boolean;
export type Name14 = string;
export type ReadOnly = boolean;
export type Email = string;
export type Id15 = string;
export type OrgId = string;
export type Role = string;
export type Email1 = string;
export type Role1 = "admin" | "builder" | "reviewer" | "viewer";
export type DisplayName = string | null;
export type Email2 = string;
/**
 * has not signed in yet
 */
export type Invited = boolean;
export type Role2 = "admin" | "builder" | "reviewer" | "viewer";
export type UserId1 = string;
export type CreatedAt4 = string | null;
export type Key = string;
export type Namespace = string[];
export type UpdatedAt1 = string | null;
export type ContextWindow = number | null;
export type Id16 = string;
export type Label3 = string;
export type PriceInPerMtok = number | null;
export type PriceOutPerMtok = number | null;
export type Provider = string;
export type Reasoning = boolean;
export type StructuredOutput = boolean;
export type Tools1 = boolean;
export type Vision = boolean;
export type Namespace1 = string;
export type Parts = string[];
export type Name15 = string;
export type Writes = string[];
export type BuiltinTools = string[];
export type Category = string;
export type ChannelFields = string[];
export type ChannelMapFields = string[];
export type Color = string | null;
export type Description4 = string;
export type Docs = string | null;
export type DynamicOutputs = "routes" | null;
export type HostedForbidden = boolean;
export type Icon = string;
export type Impl = string | null;
export type Description5 = string | null;
export type Multi = boolean;
export type Name16 = string;
export type Ordered = boolean;
export type Required1 = boolean;
export type Type6 = string;
export type Inputs = PortSpec[];
export type Interrupts1 = boolean;
export type Kind4 = "runtime" | "resource" | "middleware" | "trigger";
export type Label4 = string;
export type ListReadFields = string[];
export type ListWriteFields = string[];
export type Outputs = PortSpec[];
export type Provenance = "builtin" | "workspace";
export type ReadFields = string[];
export type Reads = string[];
export type SideEffects = ("external_write" | "message_send" | "code_exec" | "spend" | "pii_read" | "host_exec")[];
export type Stability = "stable" | "beta" | "experimental";
export type TemplateFields = string[];
export type Type7 = string;
export type Version1 = string;
export type Writes1 = string[];
export type Name17 = string;
export type CreatedAt5 = string;
export type Id17 = string;
export type Name18 = string;
export type Environment1 = string;
export type Message4 = string;
/**
 * shown once, when the deployment is created
 */
export type ApiKey1 = string | null;
export type Checklist = Check[];
export type Commands1 = {
  [k: string]: unknown;
}[];
export type DryRun1 = boolean;
export type InterruptId1 = string | null;
export type Mocks1 = {
  [k: string]: unknown[];
} | null;
export type Pins1 = {
  [k: string]: {
    [k: string]: unknown;
  };
} | null;
export type Stream1 = boolean;
export type CostUsd = number;
export type EndedAt = string | null;
export type Error2 = string | null;
export type Id18 = string;
export type IrHash1 = string;
export type Mode2 = string;
export type StartedAt = string;
export type Status5 = string;
export type ThreadId4 = string;
export type TokensIn = number;
export type TokensOut = number;
export type Traced = boolean;
export type DryRun2 = boolean;
export type Durability = ("exit" | "async" | "sync") | null;
export type Mocks2 = {
  [k: string]: unknown[];
} | null;
export type Mode3 = "normal" | "debug";
export type Pins2 = {
  [k: string]: {
    [k: string]: unknown;
  };
} | null;
export type Stream2 = boolean;
export type ThreadId5 = string | null;
export type Diagnostics2 = LintItem[];
export type Modified1 = number;
export type Path4 = string;
export type Size1 = number;
export type Value4 = string;
export type CreatedAt6 = string;
export type Name19 = string;
export type AsNode1 = string | null;
export type CheckpointId1 = string | null;
export type Name20 = string | null;
/**
 * @minItems 1
 */
export type NodeIds = [string, ...string[]];
export type Description6 = string;
export type Id19 = string;
export type Name21 = string;
export type NodeCount = number;
export type Context4 = {
  [k: string]: unknown;
} | null;
export type Name22 = string;
export type Context5 = {
  [k: string]: unknown;
} | null;
export type CreatedAt7 = string;
export type Id20 = string;
export type Name23 = string;
/**
 * run only these cases (default: all)
 */
export type Ids = string[] | null;
/**
 * run against a version instead of the draft
 */
export type Version2 = string | null;
export type Failed = number;
export type Passed1 = number;
export type Results1 = CaseResult[];
export type CreatedAt8 = string;
export type Id21 = string;
export type Status6 = string;
export type Title = string | null;
export type UpdatedAt2 = string;
export type WorkflowId1 = string;
export type Items1 = ThreadOut[];
export type NextCursor = string | null;
export type CreatedAt9 = string;
export type DeploymentId = string | null;
export type Enabled1 = boolean;
export type Id22 = string;
export type Kind5 = string;
export type Secret = string | null;
export type Url = string | null;
export type DeploymentId1 = string | null;
export type Kind6 = "webhook" | "cron";
export type CreatedAt10 = string;
export type DeploymentId2 = string | null;
export type Enabled2 = boolean;
export type Id23 = string;
export type Kind7 = string;
export type Url1 = string | null;
export type CreatedAt11 = string;
export type CreatedBy = string | null;
export type Id24 = string;
export type IrHash2 = string;
export type Message5 = string;
export type Semver1 = string;
export type WorkspaceCommit = string | null;
export type Bump = "major" | "minor" | "patch";
export type Message6 = string;
export type CreatedAt12 = string;
export type CreatedBy1 = string | null;
export type Id25 = string;
export type IrHash3 = string;
export type Message7 = string;
export type Semver2 = string;
export type WorkspaceCommit1 = string | null;
export type CreatedAt13 = string;
export type Diagnostics3 = Diagnostic[];
export type Id26 = string;
export type IrHash4 = string;
export type Name24 = string;
export type ProjectId1 = string;
export type UpdatedAt3 = string;
export type Version3 = number;
export type Id27 = string;
export type Name25 = string;
export type UpdatedAt4 = string;
export type Version4 = number;
export type Items2 = WorkflowSummary[];
export type NextCursor1 = string | null;

export interface ApiSchemas {
  AddNode?: AddNode;
  AddResource?: AddResource;
  ApiResult?: ApiResult;
  ApiResumeIn?: ApiResumeIn;
  ApiRunIn?: ApiRunIn;
  AuthConfig?: AuthConfig;
  BlastRadius?: BlastRadius;
  Breakpoints?: Breakpoints;
  CachePolicySpec?: CachePolicySpec;
  Capability?: Capability;
  CaseResult?: CaseResult;
  CatalogOut?: CatalogOut;
  Channel?: Channel;
  Check?: Check;
  CommandBatch?: CommandBatch;
  CommandResult?: CommandResult;
  Connect?: Connect;
  ContextField?: ContextField;
  ContractChange?: ContractChange;
  ContractOut?: ContractOut;
  CreateWorkflow?: CreateWorkflow;
  DebugOptions?: DebugOptions;
  Decision?: Decision;
  DeploymentOut?: DeploymentOut;
  Diagnostic?: Diagnostic;
  DiffOut?: DiffOut;
  Disconnect?: Disconnect;
  Edge?: Edge;
  ElementChange?: ElementChange;
  ElementDiff?: ElementDiff;
  EnabledIn?: EnabledIn;
  Endpoint?: Endpoint;
  EventIn?: EventIn;
  EventLogOut?: EventLogOut;
  Expect?: Expect;
  ExpressionCheck?: ExpressionCheck;
  ExpressionResult?: ExpressionResult;
  FieldChange?: FieldChange;
  FileContent?: FileContent;
  FileOut?: FileOut;
  FileSave?: FileSave;
  ForkRequest?: ForkRequest;
  FromRunIn?: FromRunIn;
  HTTPValidationError?: HTTPValidationError;
  InlineRequest?: InlineRequest;
  InterruptOut?: InterruptOut;
  KeyOut?: KeyOut;
  LayoutUpdate?: LayoutUpdate;
  LintItem?: LintItem;
  McpInspectRequest?: McpInspectRequest;
  McpToolInfo?: McpToolInfo;
  MeOut?: MeOut;
  MemberIn?: MemberIn;
  MemberOut?: MemberOut;
  MemoryItem?: MemoryItem;
  ModelInfo?: ModelInfo;
  NamespaceOut?: NamespaceOut;
  NewScript?: NewScript;
  Node?: Node;
  NodeBlast?: NodeBlast;
  NodeManifest?: NodeManifest;
  NodePolicies?: NodePolicies;
  PortSpec?: PortSpec;
  ProjectIn?: ProjectIn;
  ProjectOut?: ProjectOut;
  PublishIn?: PublishIn;
  PublishOut?: PublishOut;
  RefactorOut?: RefactorOut;
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
  SavedFile?: SavedFile;
  SecretIn?: SecretIn;
  SecretOut?: SecretOut;
  SetChannels?: SetChannels;
  SetContext?: SetContext;
  StateEdit?: StateEdit;
  SubgraphRequest?: SubgraphRequest;
  Surface?: Surface;
  TemplateInfo?: TemplateInfo;
  TestCaseIn?: TestCaseIn;
  TestCaseOut?: TestCaseOut;
  TestRunIn?: TestRunIn;
  TestRunOut?: TestRunOut;
  ThreadOut?: ThreadOut;
  ThreadPage?: ThreadPage;
  TimeoutPolicySpec?: TimeoutPolicySpec;
  TriggerCreated?: TriggerCreated;
  TriggerIn?: TriggerIn;
  TriggerOut?: TriggerOut;
  UpdateMeta?: UpdateMeta;
  UpdateNode?: UpdateNode;
  UpdateResource?: UpdateResource;
  UpdateSettings?: UpdateSettings;
  ValidationError?: ValidationError;
  VersionDetail?: VersionDetail;
  VersionIn?: VersionIn;
  VersionOut?: VersionOut;
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
 * via the `definition` "ApiResult".
 */
export interface ApiResult {
  error?: Error;
  interrupts?: Interrupts;
  output?: Output;
  run_id: RunId;
  status: Status;
  thread_id: ThreadId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ApiResumeIn".
 */
export interface ApiResumeIn {
  interrupt_id?: InterruptId;
  value?: Value;
}
export interface Value {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ApiRunIn".
 */
export interface ApiRunIn {
  context?: Context;
  input?: Input;
  thread_id?: ThreadId1;
  user_id?: UserId;
}
export interface Context {
  [k: string]: unknown;
}
export interface Input {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AuthConfig".
 */
export interface AuthConfig {
  audience?: Audience;
  client_id?: ClientId;
  issuer?: Issuer;
  mode: Mode;
  scopes?: Scopes;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "BlastRadius".
 */
export interface BlastRadius {
  nodes?: Nodes;
  summary?: Summary;
  ungated?: Ungated;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NodeBlast".
 */
export interface NodeBlast {
  capabilities?: Capabilities;
  name: Name2;
  node_id: NodeId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Capability".
 */
export interface Capability {
  effect: Effect;
  gated: Gated;
  source: Source;
}
export interface Summary {
  [k: string]: number;
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
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CaseResult".
 */
export interface CaseResult {
  duration_ms?: DurationMs;
  failures?: Failures;
  id: Id2;
  name: Name3;
  passed: Passed;
  route?: Route;
  run_id?: RunId1;
  status?: Status1;
  thread_id?: ThreadId2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CatalogOut".
 */
export interface CatalogOut {
  handlers: Handlers;
  nodes: Nodes1;
  problems: Problems;
  schemas?: Schemas;
  scripts: Scripts;
  tools: Tools;
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
  name: Name4;
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
 * via the `definition` "Check".
 */
export interface Check {
  detail?: Detail;
  items?: Items;
  name: Name5;
  ok: Ok;
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
  id: Id3;
  op?: Op2;
}
/**
 * JSON merge patch on a node (label, config, policies, disabled, metadata, name).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateNode".
 */
export interface UpdateNode {
  id: Id4;
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
  id: Id5;
  name: Name6;
  op?: Op5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RemoveResource".
 */
export interface RemoveResource {
  id: Id6;
  op?: Op6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateResource".
 */
export interface UpdateResource {
  id: Id7;
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
  id: Id8;
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
  id: Id9;
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
  name: Name7;
  required?: Required;
  source?: Source1;
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
  name?: Name8;
  op?: Op15;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CommandResult".
 */
export interface CommandResult {
  created_at: CreatedAt;
  diagnostics: Diagnostics;
  id: Id10;
  inverse: Inverse;
  ir: Ir;
  ir_hash: IrHash;
  layout: Layout;
  name: Name9;
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
  node_id?: NodeId1;
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
 * via the `definition` "ContractChange".
 */
export interface ContractChange {
  field: Field;
  message: Message1;
  severity: Severity1;
  where: Where;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ContractOut".
 */
export interface ContractOut {
  context?: Context1;
  input?: Input1;
  output?: Output1;
  pauses?: Pauses;
  required_context?: RequiredContext;
  surfaces?: Surfaces;
}
export interface Context1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface Input1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface Output1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface Pauses {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Surface".
 */
export interface Surface {
  detail: Detail1;
  kind: Kind1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CreateWorkflow".
 */
export interface CreateWorkflow {
  name?: Name10;
  template?: Template;
}
/**
 * Debug-run aids (M4-T9); never part of the workflow or its exported code.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DebugOptions".
 */
export interface DebugOptions {
  dry_run?: DryRun;
  mocks?: Mocks;
  pins?: Pins;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Decision".
 */
export interface Decision {
  value: Value1;
}
export interface Value1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DeploymentOut".
 */
export interface DeploymentOut {
  active: Active;
  created_at: CreatedAt1;
  endpoint: Endpoint1;
  environment: Environment;
  id: Id11;
  key_prefix: KeyPrefix;
  semver: Semver;
  version_id: VersionId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DiffOut".
 */
export interface DiffOut {
  a: A;
  b: B;
  channels?: ElementDiff;
  edges?: ElementDiff;
  nodes?: ElementDiff;
  resources?: ElementDiff;
  summary: Summary1;
  workflow?: Workflow;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ElementDiff".
 */
export interface ElementDiff {
  added?: Added;
  changed?: Changed;
  removed?: Removed;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ElementChange".
 */
export interface ElementChange {
  fields?: Fields1;
  id: Id12;
  name?: Name11;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FieldChange".
 */
export interface FieldChange {
  after?: After1;
  before?: Before1;
  path: Path1;
}
export interface After1 {
  [k: string]: unknown;
}
export interface Before1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EnabledIn".
 */
export interface EnabledIn {
  enabled: Enabled;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EventIn".
 */
export interface EventIn {
  data?: Data;
  type: Type4;
}
export interface Data {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EventLogOut".
 */
export interface EventLogOut {
  created_at: CreatedAt2;
  data: Data1;
  id: Id13;
  results: Results;
  source: Source2;
  status: Status2;
  type: Type5;
}
export interface Data1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Expect".
 */
export interface Expect {
  channels?: Channels1;
  route?: Route1;
  status?: Status3;
}
/**
 * exact final values of these channels
 */
export interface Channels1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionCheck".
 */
export interface ExpressionCheck {
  kind: Kind2;
  source: Source3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionResult".
 */
export interface ExpressionResult {
  error?: Error1;
  ok: Ok1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileContent".
 */
export interface FileContent {
  content: Content;
  diagnostics?: Diagnostics1;
  path: Path2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "LintItem".
 */
export interface LintItem {
  code: Code1;
  column: Column;
  end_column: EndColumn;
  end_line: EndLine;
  line: Line;
  message: Message2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileOut".
 */
export interface FileOut {
  modified: Modified;
  path: Path3;
  size: Size;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileSave".
 */
export interface FileSave {
  content: Content1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ForkRequest".
 */
export interface ForkRequest {
  as_node?: AsNode;
  checkpoint_id: CheckpointId;
  mode?: Mode1;
  stream?: Stream;
  values?: Values;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FromRunIn".
 */
export interface FromRunIn {
  name: Name12;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "HTTPValidationError".
 */
export interface HTTPValidationError {
  detail?: Detail2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ValidationError".
 */
export interface ValidationError {
  ctx?: Context2;
  input?: Input2;
  loc: Location;
  msg: Message3;
  type: ErrorType;
}
export interface Context2 {}
export interface Input2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "InlineRequest".
 */
export interface InlineRequest {
  node_id: NodeId2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "InterruptOut".
 */
export interface InterruptOut {
  canvas_node_id: CanvasNodeId;
  created_at: CreatedAt3;
  decided_at: DecidedAt;
  decision: Decision1;
  id: Id14;
  kind: Kind3;
  payload: Payload;
  run_id: RunId2;
  status: Status4;
  thread_id: ThreadId3;
  workflow_id?: WorkflowId;
  workflow_name?: WorkflowName;
}
export interface Payload {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KeyOut".
 */
export interface KeyOut {
  api_key: ApiKey;
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
 * via the `definition` "McpInspectRequest".
 */
export interface McpInspectRequest {
  config: Config2;
  name: Name13;
}
export interface Config2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "McpToolInfo".
 */
export interface McpToolInfo {
  description?: Description3;
  destructive?: Destructive;
  name: Name14;
  read_only?: ReadOnly;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MeOut".
 */
export interface MeOut {
  email: Email;
  id: Id15;
  memberships: Memberships;
  org_id: OrgId;
  role: Role;
}
export interface Memberships {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemberIn".
 */
export interface MemberIn {
  email: Email1;
  role: Role1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemberOut".
 */
export interface MemberOut {
  display_name: DisplayName;
  email: Email2;
  invited: Invited;
  role: Role2;
  user_id: UserId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemoryItem".
 */
export interface MemoryItem {
  created_at?: CreatedAt4;
  key: Key;
  namespace: Namespace;
  updated_at?: UpdatedAt1;
  value: Value2;
}
export interface Value2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelInfo".
 */
export interface ModelInfo {
  context_window?: ContextWindow;
  id: Id16;
  label: Label3;
  price_in_per_mtok?: PriceInPerMtok;
  price_out_per_mtok?: PriceOutPerMtok;
  provider: Provider;
  reasoning?: Reasoning;
  structured_output?: StructuredOutput;
  tools?: Tools1;
  vision?: Vision;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NamespaceOut".
 */
export interface NamespaceOut {
  namespace: Namespace1;
  parts: Parts;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NewScript".
 */
export interface NewScript {
  name: Name15;
  writes?: Writes;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NodeManifest".
 */
export interface NodeManifest {
  builtin_tools?: BuiltinTools;
  category: Category;
  channel_fields?: ChannelFields;
  channel_map_fields?: ChannelMapFields;
  color?: Color;
  conditional_writes?: ConditionalWrites;
  config_schema?: ConfigSchema;
  description?: Description4;
  docs?: Docs;
  dynamic_outputs?: DynamicOutputs;
  hosted_forbidden?: HostedForbidden;
  icon?: Icon;
  impl?: Impl;
  inputs?: Inputs;
  interrupts?: Interrupts1;
  kind: Kind4;
  label: Label4;
  list_read_fields?: ListReadFields;
  list_write_fields?: ListWriteFields;
  outputs?: Outputs;
  provenance?: Provenance;
  read_fields?: ReadFields;
  reads?: Reads;
  side_effects?: SideEffects;
  stability?: Stability;
  targets?: Targets;
  template_fields?: TemplateFields;
  type: Type7;
  version?: Version1;
  writes?: Writes1;
}
export interface ConditionalWrites {
  [k: string]: string[];
}
export interface ConfigSchema {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PortSpec".
 */
export interface PortSpec {
  description?: Description5;
  multi?: Multi;
  name: Name16;
  ordered?: Ordered;
  required?: Required1;
  type: Type6;
}
export interface Targets {
  [k: string]: "full" | "partial" | "none";
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectIn".
 */
export interface ProjectIn {
  name: Name17;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectOut".
 */
export interface ProjectOut {
  created_at: CreatedAt5;
  id: Id17;
  name: Name18;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PublishIn".
 */
export interface PublishIn {
  environment?: Environment1;
  message?: Message4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PublishOut".
 */
export interface PublishOut {
  api_key?: ApiKey1;
  checklist: Checklist;
  deployment: DeploymentOut;
}
/**
 * Commands the editor dispatches like its own edits (optimistic, autosaved, undoable).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RefactorOut".
 */
export interface RefactorOut {
  commands: Commands1;
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
  dry_run?: DryRun1;
  interrupt_id?: InterruptId1;
  mocks?: Mocks1;
  pins?: Pins1;
  stream?: Stream1;
  value?: Value3;
}
export interface Value3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RunOut".
 */
export interface RunOut {
  cost_usd?: CostUsd;
  ended_at: EndedAt;
  error: Error2;
  id: Id18;
  ir_hash: IrHash1;
  mode: Mode2;
  started_at: StartedAt;
  status: Status5;
  thread_id: ThreadId4;
  tokens_in?: TokensIn;
  tokens_out?: TokensOut;
  traced?: Traced;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RunRequest".
 */
export interface RunRequest {
  breakpoints?: Breakpoints | null;
  context?: Context3;
  dry_run?: DryRun2;
  durability?: Durability;
  input?: Input3;
  mocks?: Mocks2;
  mode?: Mode3;
  pins?: Pins2;
  stream?: Stream2;
  thread_id?: ThreadId5;
}
export interface Context3 {
  [k: string]: unknown;
}
export interface Input3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SavedFile".
 */
export interface SavedFile {
  diagnostics?: Diagnostics2;
  modified: Modified1;
  path: Path4;
  size: Size1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SecretIn".
 */
export interface SecretIn {
  value: Value4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SecretOut".
 */
export interface SecretOut {
  created_at: CreatedAt6;
  name: Name19;
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
 * via the `definition` "SubgraphRequest".
 */
export interface SubgraphRequest {
  name?: Name20;
  node_ids: NodeIds;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  description: Description6;
  id: Id19;
  name: Name21;
  node_count: NodeCount;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestCaseIn".
 */
export interface TestCaseIn {
  context?: Context4;
  expect?: Expect;
  input?: Input4;
  mocks?: Mocks3;
  name: Name22;
  pins?: Pins3;
}
export interface Input4 {
  [k: string]: unknown;
}
/**
 * model resource name -> replies
 */
export interface Mocks3 {
  [k: string]: unknown[];
}
/**
 * node id -> pinned output
 */
export interface Pins3 {
  [k: string]: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestCaseOut".
 */
export interface TestCaseOut {
  context?: Context5;
  created_at: CreatedAt7;
  expect?: Expect;
  id: Id20;
  input?: Input5;
  last_result?: CaseResult | null;
  mocks?: Mocks4;
  name: Name23;
  pins?: Pins4;
}
export interface Input5 {
  [k: string]: unknown;
}
/**
 * model resource name -> replies
 */
export interface Mocks4 {
  [k: string]: unknown[];
}
/**
 * node id -> pinned output
 */
export interface Pins4 {
  [k: string]: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestRunIn".
 */
export interface TestRunIn {
  ids?: Ids;
  version?: Version2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestRunOut".
 */
export interface TestRunOut {
  failed: Failed;
  passed: Passed1;
  results: Results1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadOut".
 */
export interface ThreadOut {
  created_at: CreatedAt8;
  id: Id21;
  status: Status6;
  title: Title;
  updated_at: UpdatedAt2;
  workflow_id: WorkflowId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadPage".
 */
export interface ThreadPage {
  items: Items1;
  next_cursor: NextCursor;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerCreated".
 */
export interface TriggerCreated {
  config: Config3;
  created_at: CreatedAt9;
  deployment_id: DeploymentId;
  enabled: Enabled1;
  id: Id22;
  kind: Kind5;
  secret?: Secret;
  url?: Url;
}
export interface Config3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerIn".
 */
export interface TriggerIn {
  config?: Config4;
  deployment_id?: DeploymentId1;
  kind: Kind6;
}
export interface Config4 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerOut".
 */
export interface TriggerOut {
  config: Config5;
  created_at: CreatedAt10;
  deployment_id: DeploymentId2;
  enabled: Enabled2;
  id: Id23;
  kind: Kind7;
  url?: Url1;
}
export interface Config5 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionDetail".
 */
export interface VersionDetail {
  created_at: CreatedAt11;
  created_by: CreatedBy;
  id: Id24;
  ir: Ir2;
  ir_hash: IrHash2;
  layout: Layout1;
  message: Message5;
  semver: Semver1;
  workspace_commit: WorkspaceCommit;
}
export interface Ir2 {
  [k: string]: unknown;
}
export interface Layout1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionIn".
 */
export interface VersionIn {
  bump?: Bump;
  message?: Message6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionOut".
 */
export interface VersionOut {
  created_at: CreatedAt12;
  created_by: CreatedBy1;
  id: Id25;
  ir_hash: IrHash3;
  message: Message7;
  semver: Semver2;
  workspace_commit: WorkspaceCommit1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowOut".
 */
export interface WorkflowOut {
  created_at: CreatedAt13;
  diagnostics: Diagnostics3;
  id: Id26;
  ir: Ir3;
  ir_hash: IrHash4;
  layout: Layout2;
  name: Name24;
  project_id: ProjectId1;
  updated_at: UpdatedAt3;
  version: Version3;
}
export interface Ir3 {
  [k: string]: unknown;
}
export interface Layout2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowPage".
 */
export interface WorkflowPage {
  items: Items2;
  next_cursor: NextCursor1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowSummary".
 */
export interface WorkflowSummary {
  id: Id27;
  name: Name25;
  updated_at: UpdatedAt4;
  version: Version4;
}
