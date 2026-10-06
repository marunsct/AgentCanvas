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
 * your end user's id; context fields with source 'user' get api:<deployment>:<user_id>
 */
export type UserId = string | null;
export type Action = string;
export type ActorEmail = string | null;
export type ActorId = string | null;
export type CreatedAt = string;
export type Id2 = number;
export type Ip = string | null;
export type Target = string;
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
export type Id3 = string;
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
export type Detail1 = string;
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
export type Id4 = string;
export type Op2 = "RemoveNode";
export type Id5 = string;
export type Op3 = "UpdateNode";
export type Op4 = "ReplaceNode";
export type Id6 = string;
export type Name6 = string;
export type Op5 = "RenameNode";
export type Id7 = string;
export type Op6 = "RemoveResource";
export type Id8 = string;
export type Op7 = "UpdateResource";
export type Op8 = "ReplaceResource";
export type Id9 = string;
export type Kind = "flow" | "wiring" | "data";
export type Label2 = string | null;
export type Order = number | null;
export type Node1 = string;
export type Port = string | null;
export type Index2 = number | null;
export type Op9 = "Connect";
export type Id10 = string;
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
export type CreatedAt1 = string;
export type Code = string;
export type EdgeId = string | null;
export type Hint = string | null;
export type Message = string;
export type NodeId1 = string | null;
export type Path = string | null;
export type Severity = "error" | "warning" | "info";
export type Diagnostics = Diagnostic[];
export type Id11 = string;
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
export type Detail2 = string;
export type Kind1 = string;
export type Surfaces = Surface[];
export type Name10 = string;
export type Template = string | null;
export type CreatedAt2 = string;
export type Description3 = string;
export type Examples = number;
export type Id12 = string;
export type Context2 = {
  [k: string]: unknown;
} | null;
export type Id13 = string;
export type Split = string;
/**
 * expected tool calls, in order
 */
export type Trajectory = string[] | null;
export type Items1 = Example[];
export type Name11 = string;
export type UpdatedAt1 = string;
export type Version1 = number;
export type VersionId = string;
export type WorkflowId = string;
export type Description4 = string;
export type Name12 = string;
export type CreatedAt3 = string;
export type Description5 = string;
export type Examples1 = number;
export type Id14 = string;
export type Name13 = string;
export type UpdatedAt2 = string;
export type Version2 = number;
export type WorkflowId1 = string;
export type Description6 = string | null;
export type Name14 = string | null;
export type CreatedAt4 = string;
export type CreatedBy = string | null;
export type Examples2 = number;
export type Id15 = string;
export type Message2 = string;
export type Number = number;
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
export type CreatedAt5 = string;
export type Endpoint1 = string;
export type Environment = string;
export type Id16 = string;
export type KeyPrefix = string | null;
export type Semver = string;
export type VersionId1 = string;
export type A = string;
export type B = string;
export type Added = string[];
export type Path1 = string;
export type Fields1 = FieldChange[];
export type Id17 = string;
export type Name15 = string | null;
export type Changed = ElementChange[];
export type Removed = string[];
export type Summary1 = string;
export type Workflow = FieldChange[];
export type Enabled = boolean;
export type Kind2 = "exact" | "regex" | "json_schema" | "llm_judge" | "trajectory" | "code";
export type Name16 = string;
/**
 * passes at score >= threshold
 */
export type Threshold = number | null;
export type CreatedAt6 = string;
export type EffectiveThreshold = number;
export type Id18 = string;
export type Kind3 = "exact" | "regex" | "json_schema" | "llm_judge" | "trajectory" | "code";
export type Name17 = string;
/**
 * passes at score >= threshold
 */
export type Threshold1 = number | null;
export type WorkflowId2 = string;
export type Config4 = {
  [k: string]: unknown;
} | null;
export type Name18 = string | null;
export type Threshold2 = number | null;
export type Type4 = string;
export type CreatedAt7 = string;
export type Id19 = string;
export type Results = {
  [k: string]: unknown;
}[];
export type Source2 = string;
export type Status2 = string;
export type Type5 = string;
export type Context3 = {
  [k: string]: unknown;
} | null;
export type Input3 = {
  [k: string]: unknown;
} | null;
export type Metadata3 = {
  [k: string]: unknown;
} | null;
export type Reference1 = {
  [k: string]: unknown;
} | null;
export type Split1 = string | null;
export type Trajectory1 = string[] | null;
/**
 * @minItems 1
 */
export type Examples3 = [Example, ...Example[]];
export type Message3 = string;
/**
 * node ids that must run (and no others)
 */
export type Route1 = string[] | null;
export type Status3 = "success" | "interrupted" | "error";
export type Kind4 = "cel" | "template";
export type Source3 = string;
export type Error1 = string | null;
export type Ok1 = boolean;
export type Content = string;
export type Code1 = string;
export type Column = number;
export type EndColumn = number;
export type EndLine = number;
export type Line = number;
export type Message4 = string;
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
export type Name19 = string;
/**
 * @minItems 1
 * @maxItems 500
 */
export type RunIds = [string, ...string[]];
/**
 * these test cases (default: all of the workflow's)
 */
export type Ids = string[] | null;
export type Location = (string | number)[];
export type Message5 = string;
export type ErrorType = string;
export type Detail3 = ValidationError[];
/**
 * the file's text
 */
export type Content2 = string;
export type Format = "csv" | "jsonl";
export type Split2 = string | null;
/**
 * split for examples that name none
 */
export type Split3 = string | null;
export type NodeId2 = string;
export type CanvasNodeId = string;
export type CreatedAt8 = string;
export type DecidedAt = string | null;
export type Decision1 = {
  [k: string]: unknown;
} | null;
export type Id20 = string;
export type Kind5 = string;
export type RunId2 = string;
export type Status4 = string;
export type ThreadId3 = string;
export type WorkflowId3 = string | null;
export type WorkflowName = string | null;
export type OrgId = string;
export type OrgName = string;
export type Role = string;
export type ApiKey = string;
export type Replace = boolean;
/**
 * Resource name (lets dev/test hosts substitute an in-process server)
 */
export type Name20 = string;
export type Description7 = string;
export type Destructive = boolean;
export type Name21 = string;
export type ReadOnly = boolean;
export type Email = string;
export type Id21 = string;
export type OrgId1 = string;
export type Role1 = string;
export type Email1 = string;
export type Role2 = "admin" | "builder" | "reviewer" | "viewer";
/**
 * false: belongs to another organisation and has not accepted yet
 */
export type Accepted = boolean;
export type DisplayName = string | null;
export type Email2 = string;
/**
 * has not signed in yet
 */
export type Invited = boolean;
export type Role3 = "admin" | "builder" | "reviewer" | "viewer";
export type UserId1 = string;
export type CreatedAt9 = string | null;
export type Key = string;
export type Namespace = string[];
export type UpdatedAt3 = string | null;
export type ContextWindow = number | null;
export type Id22 = string;
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
export type Name22 = string;
export type Writes = string[];
export type BuiltinTools = string[];
export type Category = string;
export type ChannelFields = string[];
export type ChannelMapFields = string[];
export type Color = string | null;
export type Description8 = string;
export type Docs = string | null;
export type DynamicOutputs = "routes" | null;
export type HostedForbidden = boolean;
export type Icon = string;
export type Impl = string | null;
export type Description9 = string | null;
export type Multi = boolean;
export type Name23 = string;
export type Ordered = boolean;
export type Required1 = boolean;
export type Type6 = string;
export type Inputs = PortSpec[];
export type Interrupts1 = boolean;
export type Kind6 = "runtime" | "resource" | "middleware" | "trigger";
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
export type Version3 = string;
export type Writes1 = string[];
export type Error2 = string | null;
export type ExpectedTrajectory = string[] | null;
export type Status5 = string;
/**
 * tool calls the run made, in order
 */
export type Trajectory2 = string[];
/**
 * days a conversation is kept after its last activity; null = the server default, 0 = forever
 */
export type RetentionDays = number | null;
/**
 * what applies now (0 = conversations are kept forever)
 */
export type EffectiveRetentionDays = number;
/**
 * days a conversation is kept after its last activity; null = the server default, 0 = forever
 */
export type RetentionDays1 = number | null;
/**
 * shown with every violation
 */
export type Description10 = string;
export type Enabled1 = boolean;
/**
 * enforce only when publishing to these (empty: always, also in the editor)
 */
export type Environments = string[];
/**
 * CEL: which elements the policy is about
 */
export type Match = string;
export type Name24 = string;
/**
 * CEL: what must hold for each of them
 */
export type Require = string;
/**
 * what each check is about: every step, every resource, or the workflow once
 */
export type Scope = "node" | "resource" | "workflow";
export type Severity2 = "error" | "warning";
export type CreatedAt10 = string;
/**
 * shown with every violation
 */
export type Description11 = string;
export type Enabled2 = boolean;
/**
 * enforce only when publishing to these (empty: always, also in the editor)
 */
export type Environments1 = string[];
export type Id23 = string;
/**
 * CEL: which elements the policy is about
 */
export type Match1 = string;
export type Name25 = string;
/**
 * CEL: what must hold for each of them
 */
export type Require1 = string;
/**
 * what each check is about: every step, every resource, or the workflow once
 */
export type Scope1 = "node" | "resource" | "workflow";
export type Severity3 = "error" | "warning";
export type Name26 = string;
export type CreatedAt11 = string;
export type Id24 = string;
export type Name27 = string;
export type Environment1 = string;
export type Message6 = string;
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
export type Error3 = string | null;
export type Id25 = string;
export type IrHash1 = string;
export type Mode2 = string;
export type StartedAt = string;
export type Status6 = string;
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
export type Comment = string;
export type Evaluator = string;
export type Passed1 = boolean;
export type Score1 = number | null;
export type Value4 = string;
export type CreatedAt12 = string;
export type Name28 = string;
export type AsNode1 = string | null;
export type CheckpointId1 = string | null;
export type Name29 = string | null;
/**
 * @minItems 1
 */
export type NodeIds = [string, ...string[]];
export type Description12 = string;
export type Id26 = string;
export type Name30 = string;
export type NodeCount = number;
export type Context6 = {
  [k: string]: unknown;
} | null;
export type Name31 = string;
export type Context7 = {
  [k: string]: unknown;
} | null;
export type CreatedAt13 = string;
export type Id27 = string;
export type Name32 = string;
/**
 * run only these cases (default: all)
 */
export type Ids1 = string[] | null;
/**
 * run against a version instead of the draft
 */
export type Version4 = string | null;
export type Failed = number;
export type Passed2 = number;
export type Results1 = CaseResult[];
export type CreatedAt14 = string;
export type Id28 = string;
export type Status7 = string;
export type Title = string | null;
export type UpdatedAt4 = string;
export type WorkflowId4 = string;
export type Items2 = ThreadOut[];
export type NextCursor = string | null;
export type CreatedAt15 = string;
export type DeploymentId = string | null;
export type Enabled3 = boolean;
export type Id29 = string;
export type Kind7 = string;
export type Secret = string | null;
export type Url = string | null;
export type DeploymentId1 = string | null;
export type Kind8 = "webhook" | "cron";
export type CreatedAt16 = string;
export type DeploymentId2 = string | null;
export type Enabled4 = boolean;
export type Id30 = string;
export type Kind9 = string;
export type Url1 = string | null;
export type CreatedAt17 = string;
export type CreatedBy1 = string | null;
export type Id31 = string;
export type IrHash2 = string;
export type Message7 = string;
export type Semver1 = string;
export type WorkspaceCommit = string | null;
export type Bump = "major" | "minor" | "patch";
export type Message8 = string;
export type CreatedAt18 = string;
export type CreatedBy2 = string | null;
export type Id32 = string;
export type IrHash3 = string;
export type Message9 = string;
export type Semver2 = string;
export type WorkspaceCommit1 = string | null;
export type CreatedAt19 = string;
export type Diagnostics3 = Diagnostic[];
export type Id33 = string;
export type IrHash4 = string;
export type Name33 = string;
export type ProjectId1 = string;
export type UpdatedAt5 = string;
export type Version5 = number;
export type Id34 = string;
export type Name34 = string;
export type UpdatedAt6 = string;
export type Version6 = number;
export type Items3 = WorkflowSummary[];
export type NextCursor1 = string | null;

export interface ApiSchemas {
  AddNode?: AddNode;
  AddResource?: AddResource;
  ApiResult?: ApiResult;
  ApiResumeIn?: ApiResumeIn;
  ApiRunIn?: ApiRunIn;
  AuditOut?: AuditOut;
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
  DatasetDetail?: DatasetDetail;
  DatasetIn?: DatasetIn;
  DatasetOut?: DatasetOut;
  DatasetPatch?: DatasetPatch;
  DatasetVersionOut?: DatasetVersionOut;
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
  EvaluatorIn?: EvaluatorIn;
  EvaluatorOut?: EvaluatorOut;
  EvaluatorPatch?: EvaluatorPatch;
  EventIn?: EventIn;
  EventLogOut?: EventLogOut;
  Example?: Example;
  ExamplePatch?: ExamplePatch;
  ExamplesIn?: ExamplesIn;
  Expect?: Expect;
  ExpressionCheck?: ExpressionCheck;
  ExpressionResult?: ExpressionResult;
  FieldChange?: FieldChange;
  FileContent?: FileContent;
  FileOut?: FileOut;
  FileSave?: FileSave;
  ForkRequest?: ForkRequest;
  FromRunIn?: FromRunIn;
  FromRunsIn?: FromRunsIn;
  FromTestCasesIn?: FromTestCasesIn;
  HTTPValidationError?: HTTPValidationError;
  ImportIn?: ImportIn;
  InlineRequest?: InlineRequest;
  InterruptOut?: InterruptOut;
  InvitationOut?: InvitationOut;
  KeyOut?: KeyOut;
  LayoutUpdate?: LayoutUpdate;
  LintItem?: LintItem;
  Mapping?: Mapping;
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
  Observation?: Observation;
  OrgSettings?: OrgSettings;
  OrgSettingsOut?: OrgSettingsOut;
  PolicyIn?: PolicyIn;
  PolicyOut?: PolicyOut;
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
  Score?: Score;
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
 * via the `definition` "AuditOut".
 */
export interface AuditOut {
  action: Action;
  actor_email: ActorEmail;
  actor_id: ActorId;
  created_at: CreatedAt;
  detail: Detail;
  id: Id2;
  ip: Ip;
  target: Target;
}
export interface Detail {
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
  id: Id3;
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
  detail?: Detail1;
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
  id: Id4;
  op?: Op2;
}
/**
 * JSON merge patch on a node (label, config, policies, disabled, metadata, name).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateNode".
 */
export interface UpdateNode {
  id: Id5;
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
  id: Id6;
  name: Name6;
  op?: Op5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RemoveResource".
 */
export interface RemoveResource {
  id: Id7;
  op?: Op6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateResource".
 */
export interface UpdateResource {
  id: Id8;
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
  id: Id9;
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
  id: Id10;
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
  created_at: CreatedAt1;
  diagnostics: Diagnostics;
  id: Id11;
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
  detail: Detail2;
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
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetDetail".
 */
export interface DatasetDetail {
  created_at: CreatedAt2;
  description: Description3;
  examples: Examples;
  id: Id12;
  items: Items1;
  name: Name11;
  splits: Splits;
  updated_at: UpdatedAt1;
  version: Version1;
  version_id: VersionId;
  workflow_id: WorkflowId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Example".
 */
export interface Example {
  context?: Context2;
  id?: Id13;
  input?: Input2;
  metadata?: Metadata2;
  reference?: Reference;
  split?: Split;
  trajectory?: Trajectory;
}
export interface Input2 {
  [k: string]: unknown;
}
export interface Metadata2 {
  [k: string]: unknown;
}
/**
 * output channel -> expected value
 */
export interface Reference {
  [k: string]: unknown;
}
/**
 * examples per split ('' = none)
 */
export interface Splits {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetIn".
 */
export interface DatasetIn {
  description?: Description4;
  name: Name12;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetOut".
 */
export interface DatasetOut {
  created_at: CreatedAt3;
  description: Description5;
  examples: Examples1;
  id: Id14;
  name: Name13;
  splits: Splits1;
  updated_at: UpdatedAt2;
  version: Version2;
  workflow_id: WorkflowId1;
}
/**
 * examples per split ('' = none)
 */
export interface Splits1 {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetPatch".
 */
export interface DatasetPatch {
  description?: Description6;
  name?: Name14;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetVersionOut".
 */
export interface DatasetVersionOut {
  created_at: CreatedAt4;
  created_by: CreatedBy;
  examples: Examples2;
  id: Id15;
  message: Message2;
  number: Number;
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
  created_at: CreatedAt5;
  endpoint: Endpoint1;
  environment: Environment;
  id: Id16;
  key_prefix: KeyPrefix;
  semver: Semver;
  version_id: VersionId1;
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
  id: Id17;
  name?: Name15;
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
 * via the `definition` "EvaluatorIn".
 */
export interface EvaluatorIn {
  config?: Config2;
  kind: Kind2;
  name: Name16;
  threshold?: Threshold;
}
export interface Config2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorOut".
 */
export interface EvaluatorOut {
  config?: Config3;
  created_at: CreatedAt6;
  effective_threshold: EffectiveThreshold;
  id: Id18;
  kind: Kind3;
  name: Name17;
  threshold?: Threshold1;
  workflow_id: WorkflowId2;
}
export interface Config3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorPatch".
 */
export interface EvaluatorPatch {
  config?: Config4;
  name?: Name18;
  threshold?: Threshold2;
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
  created_at: CreatedAt7;
  data: Data1;
  id: Id19;
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
 * via the `definition` "ExamplePatch".
 */
export interface ExamplePatch {
  context?: Context3;
  input?: Input3;
  metadata?: Metadata3;
  reference?: Reference1;
  split?: Split1;
  trajectory?: Trajectory1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExamplesIn".
 */
export interface ExamplesIn {
  examples: Examples3;
  message?: Message3;
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
  kind: Kind4;
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
  message: Message4;
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
  name: Name19;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FromRunsIn".
 */
export interface FromRunsIn {
  run_ids: RunIds;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FromTestCasesIn".
 */
export interface FromTestCasesIn {
  ids?: Ids;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "HTTPValidationError".
 */
export interface HTTPValidationError {
  detail?: Detail3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ValidationError".
 */
export interface ValidationError {
  ctx?: Context4;
  input?: Input4;
  loc: Location;
  msg: Message5;
  type: ErrorType;
}
export interface Context4 {}
export interface Input4 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ImportIn".
 */
export interface ImportIn {
  content: Content2;
  format: Format;
  /**
   * CSV only: columns -> fields
   */
  mapping?: Mapping | null;
  split?: Split3;
}
/**
 * Which CSV column fills which field. Without one: columns named like an input field fill it,
 * `reference.<channel>` columns the reference, a `split` column the split, and the rest go to metadata.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Mapping".
 */
export interface Mapping {
  input?: Input5;
  reference?: Reference2;
  split?: Split2;
}
/**
 * input field -> column
 */
export interface Input5 {
  [k: string]: string;
}
/**
 * output channel -> column
 */
export interface Reference2 {
  [k: string]: string;
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
  created_at: CreatedAt8;
  decided_at: DecidedAt;
  decision: Decision1;
  id: Id20;
  kind: Kind5;
  payload: Payload;
  run_id: RunId2;
  status: Status4;
  thread_id: ThreadId3;
  workflow_id?: WorkflowId3;
  workflow_name?: WorkflowName;
}
export interface Payload {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "InvitationOut".
 */
export interface InvitationOut {
  org_id: OrgId;
  org_name: OrgName;
  role: Role;
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
  config: Config5;
  name: Name20;
}
export interface Config5 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "McpToolInfo".
 */
export interface McpToolInfo {
  description?: Description7;
  destructive?: Destructive;
  name: Name21;
  read_only?: ReadOnly;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MeOut".
 */
export interface MeOut {
  email: Email;
  id: Id21;
  memberships: Memberships;
  org_id: OrgId1;
  role: Role1;
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
  role: Role2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemberOut".
 */
export interface MemberOut {
  accepted?: Accepted;
  display_name: DisplayName;
  email: Email2;
  invited: Invited;
  role: Role3;
  user_id: UserId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemoryItem".
 */
export interface MemoryItem {
  created_at?: CreatedAt9;
  key: Key;
  namespace: Namespace;
  updated_at?: UpdatedAt3;
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
  id: Id22;
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
  name: Name22;
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
  description?: Description8;
  docs?: Docs;
  dynamic_outputs?: DynamicOutputs;
  hosted_forbidden?: HostedForbidden;
  icon?: Icon;
  impl?: Impl;
  inputs?: Inputs;
  interrupts?: Interrupts1;
  kind: Kind6;
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
  version?: Version3;
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
  description?: Description9;
  multi?: Multi;
  name: Name23;
  ordered?: Ordered;
  required?: Required1;
  type: Type6;
}
export interface Targets {
  [k: string]: "full" | "partial" | "none";
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Observation".
 */
export interface Observation {
  error?: Error2;
  expected_trajectory?: ExpectedTrajectory;
  input?: Input6;
  output?: Output2;
  reference?: Reference3;
  status?: Status5;
  trajectory?: Trajectory2;
}
export interface Input6 {
  [k: string]: unknown;
}
export interface Output2 {
  [k: string]: unknown;
}
export interface Reference3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgSettings".
 */
export interface OrgSettings {
  retention_days?: RetentionDays;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgSettingsOut".
 */
export interface OrgSettingsOut {
  effective_retention_days: EffectiveRetentionDays;
  retention_days?: RetentionDays1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PolicyIn".
 */
export interface PolicyIn {
  description?: Description10;
  enabled?: Enabled1;
  environments?: Environments;
  match?: Match;
  name: Name24;
  require: Require;
  scope?: Scope;
  severity?: Severity2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PolicyOut".
 */
export interface PolicyOut {
  created_at: CreatedAt10;
  description?: Description11;
  enabled?: Enabled2;
  environments?: Environments1;
  id: Id23;
  match?: Match1;
  name: Name25;
  require: Require1;
  scope?: Scope1;
  severity?: Severity3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectIn".
 */
export interface ProjectIn {
  name: Name26;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectOut".
 */
export interface ProjectOut {
  created_at: CreatedAt11;
  id: Id24;
  name: Name27;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PublishIn".
 */
export interface PublishIn {
  environment?: Environment1;
  message?: Message6;
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
  error: Error3;
  id: Id25;
  ir_hash: IrHash1;
  mode: Mode2;
  started_at: StartedAt;
  status: Status6;
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
  context?: Context5;
  dry_run?: DryRun2;
  durability?: Durability;
  input?: Input7;
  mocks?: Mocks2;
  mode?: Mode3;
  pins?: Pins2;
  stream?: Stream2;
  thread_id?: ThreadId5;
}
export interface Context5 {
  [k: string]: unknown;
}
export interface Input7 {
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
 * via the `definition` "Score".
 */
export interface Score {
  comment?: Comment;
  evaluator: Evaluator;
  passed: Passed1;
  score: Score1;
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
  created_at: CreatedAt12;
  name: Name28;
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
  name?: Name29;
  node_ids: NodeIds;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  description: Description12;
  id: Id26;
  name: Name30;
  node_count: NodeCount;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestCaseIn".
 */
export interface TestCaseIn {
  context?: Context6;
  expect?: Expect;
  input?: Input8;
  mocks?: Mocks3;
  name: Name31;
  pins?: Pins3;
}
export interface Input8 {
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
  context?: Context7;
  created_at: CreatedAt13;
  expect?: Expect;
  id: Id27;
  input?: Input9;
  last_result?: CaseResult | null;
  mocks?: Mocks4;
  name: Name32;
  pins?: Pins4;
}
export interface Input9 {
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
  ids?: Ids1;
  version?: Version4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestRunOut".
 */
export interface TestRunOut {
  failed: Failed;
  passed: Passed2;
  results: Results1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadOut".
 */
export interface ThreadOut {
  created_at: CreatedAt14;
  id: Id28;
  status: Status7;
  title: Title;
  updated_at: UpdatedAt4;
  workflow_id: WorkflowId4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadPage".
 */
export interface ThreadPage {
  items: Items2;
  next_cursor: NextCursor;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerCreated".
 */
export interface TriggerCreated {
  config: Config6;
  created_at: CreatedAt15;
  deployment_id: DeploymentId;
  enabled: Enabled3;
  id: Id29;
  kind: Kind7;
  secret?: Secret;
  url?: Url;
}
export interface Config6 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerIn".
 */
export interface TriggerIn {
  config?: Config7;
  deployment_id?: DeploymentId1;
  kind: Kind8;
}
export interface Config7 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerOut".
 */
export interface TriggerOut {
  config: Config8;
  created_at: CreatedAt16;
  deployment_id: DeploymentId2;
  enabled: Enabled4;
  id: Id30;
  kind: Kind9;
  url?: Url1;
}
export interface Config8 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionDetail".
 */
export interface VersionDetail {
  created_at: CreatedAt17;
  created_by: CreatedBy1;
  id: Id31;
  ir: Ir2;
  ir_hash: IrHash2;
  layout: Layout1;
  message: Message7;
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
  message?: Message8;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionOut".
 */
export interface VersionOut {
  created_at: CreatedAt18;
  created_by: CreatedBy2;
  id: Id32;
  ir_hash: IrHash3;
  message: Message9;
  semver: Semver2;
  workspace_commit: WorkspaceCommit1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowOut".
 */
export interface WorkflowOut {
  created_at: CreatedAt19;
  diagnostics: Diagnostics3;
  id: Id33;
  ir: Ir3;
  ir_hash: IrHash4;
  layout: Layout2;
  name: Name33;
  project_id: ProjectId1;
  updated_at: UpdatedAt5;
  version: Version5;
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
  items: Items3;
  next_cursor: NextCursor1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowSummary".
 */
export interface WorkflowSummary {
  id: Id34;
  name: Name34;
  updated_at: UpdatedAt6;
  version: Version6;
}
