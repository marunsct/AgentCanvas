/* Generated from packages/ir-schema/openapi.json (pnpm gen:api). Do not edit. */

export type Index = number | null;
export type Disabled = boolean;
export type Engine = string | null;
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
export type DatasetId = string;
export type Code = string;
export type ExportProblems = string[];
export type File = string;
export type Framework = string;
export type Blocks = boolean;
export type Text = string;
export type Gaps = DesignGapOut[];
export type Label2 = string;
export type Problems = string[];
export type Api = string;
export type Hosted = boolean;
export type Id2 = string;
export type Installed = boolean;
export type Label3 = string;
export type OwnEnv = boolean;
export type Packages = string[];
export type StructuredOutput = boolean;
export type Report = string[];
export type Name2 = string | null;
export type CreatedAt = string;
export type Code1 = string;
export type EdgeId = string | null;
export type Hint = string | null;
export type Message = string;
export type NodeId = string | null;
export type Path = string | null;
export type Severity = "error" | "warning" | "info";
export type Diagnostics = Diagnostic[];
export type Id3 = string;
export type IrHash = string;
export type DatasetId1 = string | null;
export type Evaluators = number;
/**
 * already in the workspace: left as they were
 */
export type FilesKept = string[];
export type FilesWritten = string[];
export type Tests = number;
export type Triggers = number;
export type Name3 = string;
export type ProjectId = string;
export type Report1 = string[];
export type UpdatedAt = string;
export type Version = number;
export type Baseline = number | null;
export type CreatedAt1 = string;
export type DeploymentId = string;
export type Evaluator = string;
export type Id4 = string;
export type Kind = string;
export type Message1 = string;
export type ResolvedAt = string | null;
export type Value = number;
export type Comment = string;
export type Comment1 = string;
export type CreatedAt2 = string;
export type Reviewer = string;
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
export type CreatedAt3 = string;
export type Id5 = number;
export type Ip = string | null;
export type Target = string;
export type Audience = string | null;
export type ClientId = string | null;
export type Issuer = string | null;
export type Mode = "dev" | "oidc" | "matura";
export type PortalUrl = string | null;
export type Scopes = string | null;
export type TenantParam = string | null;
export type Effect = string;
export type Gated = boolean;
export type Source = string;
export type Capabilities = Capability[];
export type Name4 = string;
export type NodeId1 = string;
export type Nodes = NodeBlast[];
export type Ungated = string[];
/**
 * PDF, Word, HTML, Markdown, text, CSV or JSON files
 */
export type Files = string[];
/**
 * an image (PNG, JPEG, GIF, WebP), a PDF, a .docx or a text file
 */
export type File1 = string;
export type After = string[];
export type Before = string[];
export type Blocks1 = boolean;
export type Text1 = string;
export type Gaps1 = GapOut[];
export type Id6 = string;
export type Label4 = string;
export type Packages1 = string[];
export type Preview = boolean;
export type Runs = boolean;
export type Summary1 = string;
export type Label5 = string;
export type Why = string;
export type NotOffered = NotOfferedOut[];
export type Targets = BridgeOut[];
export type Evaluator1 = string;
export type JudgeCostUsd = number;
export type Mean = number | null;
export type PassRate = number;
export type Runs1 = number;
export type Scored = number;
export type Start = string;
export type DurationMs = number;
export type Failures = string[];
export type Id7 = string;
export type Name5 = string;
export type Passed = boolean;
export type Route = string[];
export type RunId1 = string | null;
export type Status1 = string | null;
export type ThreadId2 = string | null;
export type Handlers = {
  [k: string]: unknown;
}[];
export type Nodes1 = string[];
export type Problems1 = {
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
export type Name6 = string;
export type Reducer = "replace" | "append" | "add_messages" | "sum" | "merge";
export type Storage = "full" | "delta";
/**
 * @minItems 1
 * @maxItems 100
 */
export type Messages = [Message2, ...Message2[]];
export type Content = string;
export type Role = "user" | "assistant";
/**
 * provider:model; omitted: the project's default
 */
export type Model = string | null;
export type SystemPrompt = string;
export type Temperature = number | null;
/**
 * workspace tools the model may call, by `import_path` (GET .../workspace/catalog)
 *
 * @maxItems 20
 */
export type Tools1 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ];
/**
 * provider:model for this conversation; null: the project's
 */
export type Model1 = string | null;
export type Model2 = string;
export type Reply = string;
export type Name7 = string;
export type Result = string;
/**
 * the tool calls made for this answer, in order
 */
export type Tools2 = ToolUse[];
export type Detail1 = string;
export type Items = string[];
export type Name8 = string;
export type Ok = boolean;
export type Heading = string | null;
export type Id8 = string;
export type Ordinal = number;
export type Page = number | null;
export type Text2 = string;
/**
 * fixed windows only: characters shared
 */
export type Overlap = number;
/**
 * characters per chunk, about
 */
export type Size = number;
export type Strategy = "paragraphs" | "sentences" | "fixed" | "markdown";
export type Action1 = "write" | "explain" | "fix";
/**
 * the file as it is in the editor, unsaved edits included
 */
export type Content1 = string;
/**
 * what to write, or what to change
 */
export type Instruction = string;
export type Path1 = string;
/**
 * ruff findings shown for the file
 *
 * @maxItems 50
 */
export type Problems2 = string[];
/**
 * the selected lines, when any
 */
export type Selection = string | null;
/**
 * the whole proposed file, to show as a diff; never saved here
 */
export type Code2 = string | null;
export type Model3 = string;
/**
 * the Copilot's explanation, in plain words
 */
export type Text3 = string;
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
export type Id9 = string;
export type Op2 = "RemoveNode";
export type Id10 = string;
export type Op3 = "UpdateNode";
export type Op4 = "ReplaceNode";
export type Id11 = string;
export type Name9 = string;
export type Op5 = "RenameNode";
export type Id12 = string;
export type Op6 = "RemoveResource";
export type Id13 = string;
export type Op7 = "UpdateResource";
export type Op8 = "ReplaceResource";
export type Id14 = string;
export type Kind1 = "flow" | "wiring" | "data";
export type Label6 = string | null;
export type Order = number | null;
export type Node1 = string;
export type Port = string | null;
export type Index2 = number | null;
export type Op9 = "Connect";
export type Id15 = string;
export type Op10 = "Disconnect";
export type Channels = Channel[];
export type Op11 = "SetChannels";
export type Description1 = string | null;
export type Name10 = string;
export type Required = boolean;
export type Source1 = "input" | "user" | "tenant";
export type Fields = ContextField[];
export type Op12 = "SetContext";
export type Op13 = "UpdateSettings";
export type Op14 = "ReplaceSettings";
export type Description2 = string | null;
export type Framework1 = string | null;
export type Metadata2 = {
  [k: string]: unknown;
} | null;
export type Name11 = string | null;
export type Op15 = "UpdateMeta";
export type CreatedAt4 = string;
export type Diagnostics1 = Diagnostic[];
export type Id16 = string;
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
export type IrHash1 = string;
export type Name12 = string;
export type ProjectId1 = string;
export type UpdatedAt1 = string;
export type Version1 = number;
export type Name13 = string;
export type What = string;
export type Where = string;
export type Field = string;
export type Message3 = string;
export type Severity1 = "breaking" | "compatible";
export type Where1 = "input" | "output" | "context" | "pauses";
export type RequiredContext = string[];
export type Detail2 = string;
export type Kind2 = string;
export type Surfaces = Surface[];
export type Brief = {
  [k: string]: unknown;
} | null;
/**
 * the model chosen for this conversation; null: the project's
 */
export type ChosenModel = string | null;
/**
 * what the conversation's answers cost, where the model's price is known (each answer's own `usage` and `cost_usd` are on its message)
 */
export type CostUsd = number | null;
export type Enabled = boolean;
export type Id17 = string | null;
export type Messages1 = {
  [k: string]: unknown;
}[];
export type Model4 = string | null;
export type Origin = string | null;
/**
 * why the Copilot cannot answer yet, in plain words
 */
export type Problem = string | null;
export type PackTokens = number;
/**
 * the open proposal with its diff and preview
 */
export type Pending = {
  [k: string]: unknown;
} | null;
export type Proposals = {
  [k: string]: unknown;
}[];
export type TokenCap = number;
export type AcceptedAt = string;
export type AcceptedBy = string;
export type AcceptedByEmail = string | null;
/**
 * how many of the proposal's changes were kept
 */
export type Changes = number;
export type Summary2 = string;
/**
 * {"$secret": NAME}
 */
export type ApiKey = {
  [k: string]: string;
} | null;
/**
 * split mode: a cheaper model for Ask-mode questions
 */
export type AskModel = string | null;
export type BaseUrl = string | null;
export type Enabled1 = boolean;
/**
 * provider:model when separate
 */
export type Model5 = string | null;
/**
 * project conventions the Copilot always reads
 */
export type Notes = string;
/**
 * local_only: the Copilot may only use models running on your own machines
 */
export type Privacy = "standard" | "local_only";
export type Source2 = "workflow" | "separate";
/**
 * per conversation, in + out
 */
export type TokenCap1 = number;
export type CreatedAt5 = string;
export type Id18 = string;
export type Messages2 = number;
/**
 * the conversation the panel shows; the others are archived
 */
export type Open = boolean;
export type Title = string | null;
export type UpdatedAt2 = string;
export type Title1 = string;
export type CreatedAt6 = string;
export type Id19 = string;
export type LimitUsd = number;
export type Message4 = string;
export type Period = string;
export type PeriodStart = string;
export type RunId2 = string | null;
export type Share = number;
export type SpentUsd = number;
export type Name14 = string;
export type Template = string | null;
export type Description3 = string;
export type Name15 = string;
/**
 * normalised score (0-1) that passes
 */
export type PassAt = number;
/**
 * points; 2 is a yes/no
 */
export type Scale = number;
export type CreatedAt7 = string;
export type Description4 = string;
export type Examples = number;
export type Id20 = string;
export type Context2 = {
  [k: string]: unknown;
} | null;
export type Id21 = string;
export type Split = string;
/**
 * expected tool calls, in order
 */
export type Trajectory = string[] | null;
export type Items1 = Example[];
export type DatasetId2 = string;
export type Name16 = string;
/**
 * the version the last pull made here
 */
export type PulledVersion = number | null;
/**
 * the version last pushed
 */
export type PushedVersion = number | null;
export type SyncedAt = string;
export type Url = string | null;
export type Name17 = string;
export type UpdatedAt3 = string;
export type Version2 = number;
export type VersionId = string;
export type WorkflowId = string;
export type Description5 = string;
export type Name18 = string;
export type CreatedAt8 = string;
export type Description6 = string;
export type Examples1 = number;
export type Id22 = string;
export type Name19 = string;
export type UpdatedAt4 = string;
export type Version3 = number;
export type WorkflowId1 = string;
export type Description7 = string | null;
export type Name20 = string | null;
export type CreatedAt9 = string;
export type CreatedBy = string | null;
export type Examples2 = number;
export type Id23 = string;
export type Message5 = string;
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
export type ApiKey1 = {
  [k: string]: string;
} | null;
export type BaseUrl1 = string | null;
export type Model6 = string;
export type Active = boolean;
export type CreatedAt10 = string;
export type Endpoint1 = string;
export type Environment = string;
export type Id24 = string;
export type KeyPrefix = string | null;
export type Semver = string;
export type VersionId1 = string;
export type A = string;
export type B = string;
export type Added = string[];
export type Path2 = string;
export type Fields1 = FieldChange[];
export type Id25 = string;
export type Name21 = string | null;
export type Changed = ElementChange[];
export type Removed = string[];
export type Summary3 = string;
export type Workflow = FieldChange[];
export type Characters = number;
export type Chunks = number;
export type CreatedAt11 = string;
export type Id26 = string;
export type Name22 = string;
/**
 * upload | url | text
 */
export type Source3 = string;
export type Url1 = string | null;
/**
 * default: '<name> (copy)'
 */
export type Name23 = string | null;
/**
 * {"$secret": "NAME"}: a project secret
 */
export type ApiKey2 = {
  [k: string]: string;
} | null;
export type BaseUrl2 = string | null;
/**
 * 'builtin' (offline) or '<provider>:<model>'
 */
export type Model7 = string;
export type Enabled2 = boolean;
export type Kind3 =
  "exact" | "regex" | "json_schema" | "llm_judge" | "trajectory" | "code" | "composite" | "pairwise" | "retrieval";
export type Name24 = string;
/**
 * passes at score >= threshold
 */
export type Threshold = number | null;
export type DefaultThreshold = number;
export type Description8 = string;
export type ExperimentsOnly = boolean;
export type Kind4 = string;
export type Label7 = string;
export type CreatedAt12 = string;
export type EffectiveThreshold = number;
export type Id27 = string;
export type Kind5 =
  "exact" | "regex" | "json_schema" | "llm_judge" | "trajectory" | "code" | "composite" | "pairwise" | "retrieval";
export type Name25 = string;
/**
 * passes at score >= threshold
 */
export type Threshold1 = number | null;
export type WorkflowId2 = string;
export type Config4 = {
  [k: string]: unknown;
} | null;
export type Name26 = string | null;
export type Threshold2 = number | null;
/**
 * pairwise: 95% confidence interval of the win rate (the mean; ties count half)
 */
export type Ci = number[] | null;
export type Evaluator2 = string;
export type Mean1 = number | null;
export type PassRate1 = number | null;
export type Scored1 = number;
export type Type4 = string;
export type CreatedAt13 = string;
export type Id28 = string;
export type Results = {
  [k: string]: unknown;
}[];
export type Source4 = string;
export type Status2 = string;
export type Type5 = string;
export type Context3 = {
  [k: string]: unknown;
} | null;
export type Input3 = {
  [k: string]: unknown;
} | null;
export type Metadata4 = {
  [k: string]: unknown;
} | null;
export type Reference2 = {
  [k: string]: unknown;
} | null;
export type Split1 = string | null;
export type Trajectory1 = string[] | null;
/**
 * @minItems 1
 */
export type Examples3 = [Example, ...Example[]];
export type Message6 = string;
/**
 * node ids that must run (and no others)
 */
export type Route1 = string[] | null;
export type Status3 = "success" | "interrupted" | "error";
export type Concurrency = number;
export type DatasetId3 = string;
/**
 * default: the latest version
 */
export type DatasetVersion = number | null;
/**
 * default: every evaluator of the workflow
 */
export type EvaluatorIds = string[] | null;
export type Name27 = string | null;
/**
 * what they want; an example's metadata.goal wins
 */
export type Goal = string;
/**
 * user messages at most
 */
export type MaxTurns = number;
/**
 * catalog model that plays the user (default: the project's, Settings > Models)
 */
export type Model8 = string | null;
/**
 * who the user is and how they write
 */
export type Persona = string;
export type StopPhrase = string;
/**
 * only the examples of this split
 */
export type Split2 = string | null;
/**
 * @minItems 1
 * @maxItems 10
 */
export type Targets1 =
  | [TargetIn]
  | [TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn]
  | [TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn, TargetIn];
/**
 * the framework this target runs on; None: the workflow's own
 */
export type Framework2 = string | null;
export type Label8 = string | null;
/**
 * a published version (semver or id); None: the draft
 */
export type Version4 = string | null;
export type Concurrency1 = number;
export type CreatedAt14 = string;
export type DatasetId4 = string;
export type DatasetVersion1 = number;
export type EndedAt = string | null;
export type Error1 = string | null;
export type Evaluators1 = string[];
export type Id29 = string;
export type Name28 = string;
/**
 * pairwise judges: each target's mean is its win rate against the first target
 */
export type Pairwise = string[];
export type Split3 = string | null;
export type Status4 = string;
export type Completed = number;
export type CostUsd1 = number;
/**
 * runs that did not end in success
 */
export type Errors = number;
export type Evaluators2 = EvaluatorSummary[];
export type Examples4 = number;
/**
 * what the AI judges cost scoring this target (not in cost_usd)
 */
export type JudgeCostUsd1 = number;
/**
 * judge models without a price: their calls are not in judge_cost_usd
 */
export type JudgeUnpriced = string[];
export type Label9 = string;
export type LatencyP50Ms = number | null;
export type LatencyP95Ms = number | null;
/**
 * share of completed examples that passed every evaluator
 */
export type PassRate2 = number | null;
export type Target1 = number;
export type Tokens = number;
export type Summary4 = TargetSummary[];
export type Framework3 = string | null;
export type Label10 = string;
export type Version5 = string | null;
export type Targets2 = TargetOut[];
export type WorkflowId3 = string;
export type CostUsd2 = number;
export type Error2 = string | null;
export type ExampleId = string;
export type Id30 = string;
export type LatencyMs = number | null;
export type Output2 = {
  [k: string]: unknown;
} | null;
export type Position = number;
export type RunId3 = string | null;
export type RunStatus = string | null;
export type Comment2 = string;
export type Evaluator3 = string;
export type Passed1 = boolean;
export type Score1 = number | null;
export type Scores2 = Score[];
export type Status5 = string;
export type Target2 = number;
export type ThreadId3 = string | null;
export type TokensIn = number;
export type TokensOut = number;
export type Trajectory2 = string[] | null;
export type Id31 = string;
export type Label11 = string;
export type Summary5 = string;
export type Targets3 = ExportTargetOut[];
export type Kind6 = "cel" | "template";
export type Source5 = string;
export type Error3 = string | null;
export type Ok1 = boolean;
export type Comment3 = string;
export type Rating = "up" | "down";
export type Content2 = string;
export type Code3 = string;
export type Column = number;
export type EndColumn = number;
export type EndLine = number;
export type Line = number;
export type Message7 = string;
export type Diagnostics2 = LintItem[];
export type Path3 = string;
/**
 * the file to move
 */
export type Path4 = string;
/**
 * its new path (a new name, or another folder)
 */
export type To = string;
export type Content3 = string;
export type Modified = number;
export type Path5 = string;
export type Size1 = number;
export type Files1 = FileOut[];
/**
 * the workflow's own code folder, when `workflow_id` was given
 */
export type Folder = string | null;
/**
 * only runs that ended so (default: any)
 */
export type Status6 = ("success" | "interrupted" | "error" | "cancelled")[] | null;
export type AsNode = string | null;
export type CheckpointId = string;
export type Mode1 = "branch" | "copy";
export type Stream = boolean;
export type Values = {
  [k: string]: unknown;
} | null;
export type Abilities = (
  "thread_state" | "time_travel" | "memory_store" | "mcp_inspect" | "breakpoints" | "agent_designs"
)[];
export type Description9 = string;
export type Framework4 = string;
export type Id32 = string;
export type Label12 = string;
export type Languages = string[];
/**
 * its runtime is set up here, so its workflows run (else see `setup`)
 */
export type Ready = boolean;
/**
 * what to do so its workflows run on this server
 */
export type Setup = string | null;
export type Tier = "T1" | "T2" | "T3" | "community";
export type Version6 = string;
export type Name29 = string;
/**
 * @minItems 1
 * @maxItems 500
 */
export type RunIds = [string, ...string[]];
/**
 * these test cases (default: all of the workflow's)
 */
export type Ids = string[] | null;
export type DatasetId5 = string;
/**
 * the dataset version to gate on (default: latest)
 */
export type DatasetVersion2 = number | null;
/**
 * largest allowed drop in pass rate against the live version
 */
export type MaxRegression = number | null;
export type DatasetId6 = string;
/**
 * the dataset version to gate on (default: latest)
 */
export type DatasetVersion3 = number | null;
export type Environment1 = string;
/**
 * the evaluators the gate measures with, as saved (null: a gate saved before they were kept)
 */
export type Evaluators3 = string[] | null;
export type Id33 = string;
/**
 * largest allowed drop in pass rate against the live version
 */
export type MaxRegression1 = number | null;
export type UpdatedAt5 = string;
export type WorkflowId4 = string;
/**
 * created from the default branch if it is new
 */
export type Branch = string;
/**
 * where the export goes in the repository ('' = the root)
 */
export type Folder1 = string;
/**
 * the commit message
 */
export type Message8 = string | null;
/**
 * owner/name of the repository
 */
export type Repo = string;
export type Target3 = "local" | "server" | "kubernetes" | "cloudrun" | "aws" | "azure";
/**
 * project secret
 */
export type TokenSecret = string;
export type Version7 = string | null;
export type Branch1 = string;
export type Commit = string;
export type CreatedBranch = boolean;
export type Files2 = number;
export type Url2 = string;
/**
 * open or update the pull request on every publish, every version, or never
 */
export type Auto = "off" | "publish" | "version";
/**
 * the branch the pull requests come from
 */
export type Branch2 = string;
/**
 * where the workflow goes in the repository ('' = the root)
 */
export type Folder2 = string;
/**
 * owner/name of the repository
 */
export type Repo1 = string;
export type Target4 = "local" | "server" | "kubernetes" | "cloudrun" | "aws" | "azure";
/**
 * project secret
 */
export type TokenSecret1 = string;
export type Auto1 = "off" | "publish" | "version";
export type Branch3 = string;
export type Folder3 = string;
export type LastError = string | null;
export type LastSyncedAt = string | null;
/**
 * the version (or 'draft') the branch was last given
 */
export type LastVersion = string | null;
export type PrNumber = number | null;
export type PrUrl = string | null;
export type Repo2 = string;
export type Target5 = string;
export type TokenSecret2 = string;
export type Error4 = string | null;
export type PrNumber1 = number | null;
export type PrUrl1 = string | null;
export type Markdown = string;
export type Page1 = string;
export type Title2 = string;
export type Page2 = string;
export type Title3 = string;
export type Location = (string | number)[];
export type Message9 = string;
export type ErrorType = string;
export type Detail3 = ValidationError[];
/**
 * hostnames, or `*.example.com`; no scheme, port or path
 *
 * @minItems 1
 */
export type Hosts = [string, ...string[]];
export type Name30 = string;
/**
 * path prefixes to match (empty: every path)
 */
export type Paths = string[];
/**
 * OpenAI Agents SDK Python (an Agent Builder export, or written by hand)
 */
export type Code4 = string | null;
/**
 * The JSON a Langflow, Flowise, n8n or Agent Spec export downloads
 */
export type File2 = {
  [k: string]: unknown;
} | null;
export type Name31 = string | null;
/**
 * the file's text
 */
export type Content4 = string;
export type Format = "csv" | "jsonl";
export type Split4 = string | null;
/**
 * split for examples that name none
 */
export type Split5 = string | null;
export type CreatedAt15 = string;
export type Diagnostics3 = Diagnostic[];
/**
 * langflow, flowise, n8n, agentspec or openai_agents
 */
export type Format1 = string;
export type Id34 = string;
export type IrHash2 = string;
export type Name32 = string;
export type ProjectId2 = string;
export type Report2 = string[];
export type UpdatedAt6 = string;
export type Version8 = number;
export type NodeId2 = string;
/**
 * true: the field is a message list; each message is appended to it
 */
export type Chat = boolean;
/**
 * the input field the visitor's message goes to
 */
export type Field1 = string | null;
export type CanvasNodeId = string;
export type CreatedAt16 = string;
export type DecidedAt = string | null;
export type Decision1 = {
  [k: string]: unknown;
} | null;
export type Id35 = string;
export type Kind7 = string;
/**
 * the waiting block's label (or name) in the workflow's draft
 */
export type NodeLabel = string | null;
/**
 * the workflow's project: the Inbox spans the organisation
 */
export type ProjectId3 = string | null;
export type ProjectName = string | null;
export type RunId4 = string;
export type Status7 = string;
export type ThreadId4 = string;
export type WorkflowId5 = string | null;
export type WorkflowName = string | null;
export type OrgId = string;
export type OrgName = string;
export type Role1 = string;
export type Annotations = AnnotationOut[];
export type CreatedAt17 = string;
export type Error5 = string | null;
export type Id36 = string;
export type Input6 = {
  [k: string]: unknown;
} | null;
export type Output3 = {
  [k: string]: unknown;
} | null;
export type QueueId = string;
export type Reason = string;
export type ReviewedByMe = boolean;
export type Reviews = number;
export type RunId5 = string;
export type RunStatus1 = string;
export type Comment4 = string;
export type CreatedAt18 = string;
export type CreatedBy1 = string | null;
export type Evaluator4 = string;
export type Id37 = string;
export type JudgeCostUsd2 = number;
export type JudgeTokens = number;
export type Passed2 = boolean;
export type RunId6 = string;
export type Score2 = number | null;
export type Source6 = string;
export type ThreadId5 = string;
/**
 * every score the run has: online evaluators, feedback, reviewers
 */
export type Scores3 = ScoreOut[];
export type Status8 = string;
export type ThreadId6 = string;
export type CreatedAt19 = string;
export type Id38 = string;
export type QueueId1 = string;
export type Reason1 = string;
export type ReviewedByMe1 = boolean;
export type Reviews1 = number;
export type RunId7 = string;
export type Status9 = string;
export type ThreadId7 = string;
/**
 * @minItems 1
 * @maxItems 100
 */
export type RunIds1 = [string, ...string[]];
export type ApiKey3 = string;
export type Description10 = string;
/**
 * lower-case letters, digits, '-', '_'
 */
export type Name33 = string;
/**
 * load a sample knowledge base instead
 */
export type Sample = "handbook" | null;
export type Chunks1 = number;
export type CreatedAt20 = string;
export type Description11 = string;
export type Documents = number;
export type Id39 = string;
export type Name34 = string;
export type ProjectId4 = string;
export type EveryHours = number | null;
export type Added1 = number;
export type At = string;
/**
 * [{url, error}]: pages that could not be fetched or added
 */
export type Failed = {
  [k: string]: string;
}[];
export type Removed1 = number;
export type Unchanged = number;
export type Updated = number;
export type Sitemaps = string[];
/**
 * settings changed since the documents were indexed: reindex
 */
export type Stale = boolean;
export type UpdatedAt7 = string;
export type Version9 = number;
export type Description12 = string | null;
export type Name35 = string | null;
export type Replace = boolean;
export type Auth = "none" | "key" | "oauth";
export type Category = string;
export type Description13 = string;
export type Docs = string;
export type Id40 = string;
export type Local = boolean;
export type Name36 = string;
export type Publisher = "official" | "vendor" | "community";
export type Secrets = ConnectorSecret[];
export type Setup1 = string;
export type Source7 = string;
export type Verified = string;
/**
 * Resource name (lets dev/test hosts substitute an in-process server)
 */
export type Name37 = string;
export type Description14 = string;
export type Destructive = boolean;
export type Name38 = string;
export type ReadOnly = boolean;
export type Email = string;
export type Id41 = string;
export type OrgId1 = string;
export type PortalUrl1 = string | null;
/**
 * the editor opens in Simple mode
 */
export type SimpleMode = boolean | null;
export type Theme = ("system" | "light" | "medium" | "dark") | null;
export type Role2 = string;
export type Email1 = string;
export type Role3 = "admin" | "builder" | "reviewer" | "viewer";
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
export type Role4 = "admin" | "builder" | "reviewer" | "viewer";
export type UserId1 = string;
export type CreatedAt21 = string | null;
export type Key = string;
export type Namespace = string[];
export type UpdatedAt8 = string | null;
/**
 * the person allows test runs that call real models
 */
export type AllowRealRuns = boolean;
export type Mode2 = "build" | "ask";
export type Text4 = string;
export type Embedding2 = string | null;
export type Judge = string | null;
export type SimulatedUser = string | null;
/**
 * provider:model, or null when nothing is chosen
 */
export type Model9 = string | null;
/**
 * where the choice comes from, in the person's words
 */
export type Origin1 = string;
/**
 * what to choose when there is no model
 */
export type Problem1 = string | null;
export type Purpose = string;
/**
 * every purpose: its model and where it comes from
 */
export type Resolved = Resolved1[];
export type ContextWindow = number | null;
export type Id42 = string;
export type Label13 = string;
export type PriceCacheReadPerMtok = number | null;
export type PriceCacheWritePerMtok = number | null;
export type PriceInPerMtok = number | null;
export type PriceOutPerMtok = number | null;
export type Provider = string;
export type Reasoning = boolean;
export type Retires = string | null;
export type Sampling = boolean;
export type StructuredOutput1 = boolean;
export type Tools3 = boolean;
export type Vision = boolean;
/**
 * provider:model, or 'builtin' for the built-in embedding
 */
export type Id43 = string;
export type Label14 = string;
export type Local1 = boolean;
/**
 * the provider's name, e.g. LM Studio (local)
 */
export type Provider1 = string;
export type Namespace1 = string;
export type Parts = string[];
export type Name39 = string;
/**
 * create it in this workflow's own folder
 */
export type WorkflowId6 = string | null;
export type Writes = string[];
export type BuiltinTools = string[];
export type Category1 = string;
export type ChannelFields = string[];
export type ChannelMapFields = string[];
export type Color = string | null;
export type Description15 = string;
export type Docs1 = string | null;
export type DynamicOutputs = "routes" | null;
export type HostedForbidden = boolean;
export type Icon = string;
export type Impl = string | null;
export type Description16 = string | null;
export type Multi = boolean;
export type Name40 = string;
export type Ordered = boolean;
export type Required1 = boolean;
export type Type6 = string;
export type Inputs = PortSpec[];
export type Interrupts1 = boolean;
export type Kind8 = "runtime" | "resource" | "middleware" | "trigger";
export type Label15 = string;
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
export type Version10 = string;
export type Writes1 = string[];
export type Error6 = string | null;
export type ExpectedTrajectory = string[] | null;
export type Status10 = string;
/**
 * tool calls the run made, in order
 */
export type Trajectory3 = string[];
/**
 * alert when the window mean is below
 */
export type AlertBelow = number | null;
/**
 * alert when the window mean is this far below the trailing 7-day mean
 */
export type AlertDrop = number | null;
export type EvaluatorId = string;
/**
 * scored runs a window needs before rules apply
 */
export type MinSamples = number;
/**
 * share of matching runs scored
 */
export type Sampling1 = number;
export type WindowMinutes = number;
/**
 * alert when the window mean is below
 */
export type AlertBelow1 = number | null;
/**
 * alert when the window mean is this far below the trailing 7-day mean
 */
export type AlertDrop1 = number | null;
export type CreatedAt22 = string;
export type DeploymentId1 = string;
export type Evaluator5 = string;
export type EvaluatorId1 = string;
export type Id44 = string;
/**
 * scored runs a window needs before rules apply
 */
export type MinSamples1 = number;
/**
 * share of matching runs scored
 */
export type Sampling2 = number;
export type WindowMinutes1 = number;
export type AlertBelow2 = number | null;
export type AlertDrop2 = number | null;
export type MinSamples2 = number | null;
export type Sampling3 = number | null;
export type WindowMinutes2 = number | null;
/**
 * false: no local MCP servers, even if the server allows
 */
export type AllowLocalMcp = boolean | null;
/**
 * not below the server's; 0 = forever
 */
export type AuditRetentionDays = number | null;
/**
 * 0 = forever
 */
export type MemoryRetentionDays = number | null;
/**
 * calls a minute, each deployment
 */
export type RateLimitPerDeployment = number | null;
/**
 * runs a minute per project
 */
export type RateLimitPerProject = number | null;
/**
 * messages a minute per share-link visitor
 */
export type RateLimitPerShareClient = number | null;
/**
 * calls a minute per webhook
 */
export type RateLimitPerWebhook = number | null;
/**
 * where workflows are published; null: any name (dev, staging, prod offered)
 */
export type Environments =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | null;
/**
 * the organisation's name
 */
export type Name41 = string | null;
/**
 * days a conversation is kept after its last activity; null = the server default, 0 = forever
 */
export type RetentionDays = number | null;
/**
 * the server's: audit-log rows older than this go (0 = kept)
 */
export type AuditRetentionDays1 = number;
/**
 * what applies now (0 = conversations are kept forever)
 */
export type EffectiveRetentionDays = number;
/**
 * the organisation's list, or null when none is set
 */
export type Environments1 = string[] | null;
/**
 * the server's: memories not updated for this long go (0 = kept)
 */
export type MemoryRetentionDays1 = number;
export type Name42 = string;
/**
 * false in Matura mode, where organisations are named in Matura
 */
export type NameEditable = boolean;
/**
 * what the web app offers when publishing
 */
export type OfferedEnvironments = string[];
export type RetentionDays1 = number | null;
/**
 * shown with every violation
 */
export type Description17 = string;
export type Enabled3 = boolean;
/**
 * enforce only when publishing to these (empty: always, also in the editor)
 */
export type Environments2 = string[];
/**
 * CEL: which elements the policy is about
 */
export type Match = string;
export type Name43 = string;
/**
 * CEL: what must hold for each of them
 */
export type Require = string;
/**
 * what each check is about: every step, every resource, or the workflow once
 */
export type Scope = "node" | "resource" | "workflow";
export type Severity2 = "error" | "warning";
export type CreatedAt23 = string;
/**
 * shown with every violation
 */
export type Description18 = string;
export type Enabled4 = boolean;
/**
 * enforce only when publishing to these (empty: always, also in the editor)
 */
export type Environments3 = string[];
export type Id45 = string;
/**
 * CEL: which elements the policy is about
 */
export type Match1 = string;
export type Name44 = string;
/**
 * CEL: what must hold for each of them
 */
export type Require1 = string;
/**
 * what each check is about: every step, every resource, or the workflow once
 */
export type Scope1 = "node" | "resource" | "workflow";
export type Severity3 = "error" | "warning";
export type Adapter = string;
export type Blockers = {
  [k: string]: unknown;
}[];
export type Emulated = {
  [k: string]: unknown;
}[];
export type Label16 = string;
export type Ok2 = boolean;
export type Done = number;
export type Pending1 = number;
export type Total = number;
export type Name45 = string;
export type WorkspaceId = string | null;
export type CreatedAt24 = string;
export type Id46 = string;
export type Name46 = string;
export type WorkspaceId1 = string | null;
export type Name47 = string;
export type WorkspaceId2 = string | null;
/**
 * accepted: the indices of the changes kept (the editor applied those); null = all
 */
export type Commands1 = number[] | null;
/**
 * accepted: the indices of the files to write; null = all
 */
export type Files3 = number[] | null;
/**
 * accepted: add the proposal's notes to the project's Copilot notes
 */
export type Notes2 = boolean;
export type Status11 = "accepted" | "rejected";
/**
 * accepted: the indices of the tests to save; null = all
 */
export type Tests1 = number[] | null;
export type BaseUrl3 = string | null;
export type BaseUrlRequired = boolean;
export type Example1 = string;
export type Id47 = string;
export type Installed1 = boolean;
export type KeyEnv = string | null;
export type Label17 = string;
export type Local2 = boolean;
export type Module = string;
export type Notes3 = string;
export type OptionalKeyEnv = string | null;
export type Package = string;
export type Environment2 = string;
export type Message10 = string;
/**
 * admins: publish although the release gate fails (audited)
 */
export type OverrideReason = string | null;
/**
 * shown once: when the deployment is created, or published again after an unpublish
 */
export type ApiKey4 = string | null;
export type Checklist = Check[];
/**
 * replace the draft even though it changed since the last sync
 */
export type Overwrite = boolean;
export type Outcome = "applied" | "same" | "unchanged";
/**
 * what the import changed in the draft
 */
export type Summary6 = string;
/**
 * the draft's version after the import
 */
export type Version11 = number;
export type Base = string;
export type Branch4 = string;
/**
 * false: the branch already held exactly this export
 */
export type Changed1 = boolean;
export type Commit1 = string;
export type CreatedBranch1 = boolean;
export type Label18 = string;
export type Note = string | null;
export type PrCreated = boolean;
export type PrNumber2 = number | null;
export type PrUrl2 = string | null;
export type Instructions = string;
export type Name48 = string;
export type ReviewersPerItem = number;
/**
 * @minItems 1
 * @maxItems 20
 */
export type Rubric =
  | [Criterion]
  | [Criterion, Criterion]
  | [Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ];
/**
 * production runs that failed
 */
export type Failed1 = boolean;
/**
 * runs an end user rated down through the deployment API
 */
export type ThumbsDown = boolean;
export type CreatedAt25 = string;
export type Id48 = string;
export type Instructions1 = string;
export type Name49 = string;
export type ReviewersPerItem1 = number;
/**
 * @minItems 1
 * @maxItems 20
 */
export type Rubric1 =
  | [Criterion]
  | [Criterion, Criterion]
  | [Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion, Criterion]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ]
  | [
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion,
      Criterion
    ];
export type WorkflowId7 = string;
export type Instructions2 = string | null;
export type Name50 = string | null;
export type ReviewersPerItem2 = number | null;
/**
 * fetch the web pages again this often; null turns it off
 */
export type EveryHours1 = number | null;
export type Commands2 = {
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
export type CostUsd3 = number;
export type EndedAt1 = string | null;
export type Error7 = string | null;
export type Id49 = string;
export type IrHash3 = string;
export type Mode3 = string;
export type StartedAt = string;
export type Status12 = string;
export type ThreadId8 = string;
export type TokensIn1 = number;
export type TokensOut1 = number;
export type Traced = boolean;
export type DryRun2 = boolean;
export type Durability = ("exit" | "async" | "sync") | null;
export type Mocks2 = {
  [k: string]: unknown[];
} | null;
export type Mode4 = "normal" | "debug";
export type Pins2 = {
  [k: string]: {
    [k: string]: unknown;
  };
} | null;
export type Stream2 = boolean;
export type ThreadId9 = string | null;
/**
 * inject rules add their headers (LangSmith only)
 */
export type AppliesInject = boolean;
/**
 * an egress list gives code exactly those hosts
 */
export type EnforcesEgress = boolean;
export type Error8 = string | null;
/**
 * how this server runs workspace code (AGENTCANVAS_SCRIPT_EXECUTION)
 */
export type Mode5 = string;
/**
 * allow-list: host, host:port, *.domain; [] = offline
 *
 * @maxItems 100
 */
export type Egress = string[];
export type Inject = HeaderRule[];
export type MemoryMb = number;
export type TimeoutS = number;
export type Problems3 = LintItem[];
/**
 * an egress list makes this server refuse to run the code
 */
export type RefusesEgress = boolean;
export type Diagnostics4 = LintItem[];
export type Modified1 = number;
export type Path6 = string;
export type Size2 = number;
export type RunId8 = string;
export type Scores4 = ScoreOut[];
export type StartedAt1 = string;
export type Status13 = string;
export type ThreadId10 = string;
/**
 * 0-1 in every mode (see the guide)
 */
export type MinScore = number;
export type Mode6 = "hybrid" | "semantic" | "keyword";
export type Query = string;
export type TopK = number;
export type ChunkId = string;
export type Cite = number;
export type DocId = string | null;
export type Heading1 = string | null;
export type Page3 = number | null;
export type Score3 = number;
export type Source8 = string | null;
export type Text5 = string;
export type Title4 = string | null;
export type Results1 = SearchResult[];
export type Value5 = string;
export type CreatedAt26 = string;
export type Name51 = string;
/**
 * used for this request only; omit to use the saved key
 */
export type ApiKey5 = string | null;
export type BaseUrl4 = string | null;
export type Provider2 = string;
export type Error9 = string | null;
/**
 * model names, without the provider prefix
 */
export type Models = string[];
export type Ok3 = boolean;
/**
 * the environment variable an operator sets it with
 */
export type Env = string;
export type Group = string;
export type Label19 = string;
export type Value6 = string;
/**
 * {"$secret": NAME}
 */
export type ApiKey6 = {
  [k: string]: string;
} | null;
/**
 * split mode: a cheaper model for Ask-mode questions
 */
export type AskModel1 = string | null;
export type BaseUrl5 = string | null;
export type Enabled5 = boolean;
/**
 * provider:model when separate
 */
export type Model10 = string | null;
/**
 * project conventions the Copilot always reads
 */
export type Notes4 = string;
/**
 * local_only: the Copilot may only use models running on your own machines
 */
export type Privacy1 = "standard" | "local_only";
export type Source9 = "workflow" | "separate";
/**
 * per conversation, in + out
 */
export type TokenCap2 = number;
/**
 * stored as a project secret; omit to keep
 */
export type ApiKey7 = string | null;
export type BaseUrl6 = string | null;
/**
 * provider:model, e.g. ollama:llama3.3
 */
export type Model11 = string;
export type Done1 = boolean;
/**
 * the project secret the default model's key is read from
 */
export type KeySecret = string | null;
/**
 * that secret exists, or the server has the variable
 */
export type KeySet = boolean;
export type Comment5 = string;
export type Rating1 = "up" | "down";
/**
 * a random secret the visitor's browser keeps; only it continues the conversations it started
 */
export type Visitor = string;
export type Greeting = string;
export type Suggestions = string[];
export type Title5 = string;
export type DeploymentId2 = string;
export type Enabled6 = boolean;
/**
 * sites allowed to show the chat page in a frame; empty: any site
 */
export type FrameOrigins = string[];
export type Greeting1 = string;
export type InputField = string | null;
/**
 * what `input_field` may name: the text inputs and the message list
 */
export type Inputs1 = string[];
/**
 * the chat page, relative to the web app (/s/<token>); none without a token
 */
export type Path7 = string | null;
export type Suggestions1 = string[];
export type Title6 = string;
/**
 * none until the settings are first saved (switching sharing on makes it)
 */
export type Token = string | null;
export type UpdatedAt9 = string | null;
/**
 * not used: approvals cannot be given on a share link
 */
export type Approve = boolean | null;
export type InterruptId2 = string | null;
/**
 * the answer to a question
 */
export type Text6 = string | null;
/**
 * a random secret the visitor's browser keeps; only it continues the conversations it started
 */
export type Visitor1 = string;
export type Message11 = string;
/**
 * continue one of this visitor's conversations
 */
export type ThreadId11 = string | null;
/**
 * a random secret the visitor's browser keeps; only it continues the conversations it started
 */
export type Visitor2 = string;
export type Enabled7 = boolean | null;
/**
 * sites allowed to show the chat page in a frame (the chat widget); empty: any site
 */
export type FrameOrigins1 = string[] | null;
export type Greeting2 = string | null;
/**
 * '' picks one from the workflow's inputs
 */
export type InputField1 = string | null;
export type Suggestions2 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | null;
export type Title7 = string | null;
/**
 * a sitemap.xml (or a sitemap index) on a public host
 */
export type Url3 = string;
export type Alerts = CostAlertOut[];
export type CacheHits = number;
export type Fallbacks = number;
export type LimitUsd1 = number | null;
export type Period1 = string;
export type PeriodStart1 = string;
export type Runs2 = number;
export type SpentUsd1 = number;
export type Tokens1 = number;
/**
 * model answers taken from the answer cache
 */
export type CacheHits1 = number;
export type CostUsd4 = number;
export type Email3 = string | null;
/**
 * model calls a backup model answered
 */
export type Fallbacks1 = number;
/**
 * null: runs of no person
 */
export type Id50 = string | null;
export type Name52 = string;
export type Runs3 = number;
export type TokensIn2 = number;
export type TokensOut2 = number;
/**
 * model answers taken from the answer cache
 */
export type CacheHits2 = number;
export type CostUsd5 = number;
/**
 * model calls a backup model answered
 */
export type Fallbacks2 = number;
/**
 * null: runs of no person
 */
export type Id51 = string | null;
export type Name53 = string;
export type Runs4 = number;
export type TokensIn3 = number;
export type TokensOut3 = number;
/**
 * model answers taken from the answer cache
 */
export type CacheHits3 = number;
export type CostUsd6 = number;
/**
 * model calls a backup model answered
 */
export type Fallbacks3 = number;
export type Runs5 = number;
export type TokensIn4 = number;
export type TokensOut4 = number;
/**
 * model answers taken from the answer cache
 */
export type CacheHits4 = number;
export type CostUsd7 = number;
/**
 * model calls a backup model answered
 */
export type Fallbacks4 = number;
/**
 * null: runs of no person
 */
export type Id52 = string | null;
export type Name54 = string;
export type ProjectId5 = string;
export type Runs6 = number;
export type TokensIn5 = number;
export type TokensOut5 = number;
export type ByPerson = SpendPerson[];
export type ByProject = SpendProject[];
export type ByWorkflow = SpendWorkflow[];
export type Since = string;
export type Until = string;
export type AsNode1 = string | null;
export type CheckpointId1 = string | null;
/**
 * a turn was answering on this server and has been ended; its text is kept
 */
export type Stopped = boolean;
export type Name55 = string | null;
/**
 * @minItems 1
 */
export type NodeIds = [string, ...string[]];
export type BlockCount = number;
export type Category2 = string | null;
export type Description19 = string;
export type Difficulty = ("beginner" | "intermediate" | "advanced") | null;
export type Id53 = string;
/**
 * what it adds beside the workflow, e.g. '2 workspace files', 'a dataset'
 */
export type Kit = string[];
export type Name56 = string;
export type NodeCount = number;
export type SampleInput = {
  [k: string]: unknown;
} | null;
export type Tags = string[];
export type Context6 = {
  [k: string]: unknown;
} | null;
export type Name57 = string;
export type Context7 = {
  [k: string]: unknown;
} | null;
export type CreatedAt27 = string;
export type Id54 = string;
export type Name58 = string;
/**
 * run only these cases (default: all)
 */
export type Ids1 = string[] | null;
/**
 * run against a version instead of the draft
 */
export type Version12 = string | null;
export type Failed2 = number;
export type Passed3 = number;
export type Results2 = CaseResult[];
export type Text7 = string;
export type Title8 = string;
export type CostUsd8 = number;
export type CreatedAt28 = string;
/**
 * set when the conversation came through a deployment's API
 */
export type DeploymentId3 = string | null;
export type Id55 = string;
export type LastEndedAt = string | null;
export type LastError1 = string | null;
export type LastRunId = string | null;
export type LastStartedAt = string | null;
export type Runs7 = number;
export type Status14 = string;
export type Title9 = string | null;
export type Tokens2 = number;
export type UpdatedAt10 = string;
export type WorkflowId8 = string;
export type WorkflowName1 = string | null;
export type Items2 = ThreadOut[];
export type NextCursor = string | null;
/**
 * Phoenix API key, as a secret (optional)
 */
export type ApiKey8 = {
  [k: string]: string;
} | null;
export type Enabled8 = boolean;
/**
 * base URL (the full traces URL for otlp); empty = default
 */
export type Endpoint2 = string;
/**
 * send inputs, outputs and model replies (masked)
 */
export type IncludeContent = boolean;
export type Kind9 = "langfuse" | "phoenix" | "datadog" | "otlp";
/**
 * Langfuse public key, as a secret
 */
export type PublicKey = {
  [k: string]: string;
} | null;
/**
 * Langfuse secret key, as a secret
 */
export type SecretKey = {
  [k: string]: string;
} | null;
export type MissingSecrets = string[];
export type CreatedAt29 = string;
export type DeploymentId4 = string | null;
export type Enabled9 = boolean;
export type Id56 = string;
export type Kind10 = string;
export type Secret = string | null;
export type Url4 = string | null;
export type DeploymentId5 = string | null;
export type Kind11 = "webhook" | "cron";
export type CreatedAt30 = string;
export type DeploymentId6 = string | null;
export type Enabled10 = boolean;
export type Id57 = string;
export type Kind12 = string;
export type Url5 = string | null;
export type Url6 = string;
/**
 * the Copilot proposals accepted since the version before, and by whom
 */
export type CopilotChanges = CopilotChange[];
export type CreatedAt31 = string;
export type CreatedBy2 = string | null;
export type Id58 = string;
export type IrHash4 = string;
export type Message12 = string;
export type Semver1 = string;
export type WorkspaceCommit = string | null;
export type Bump = "major" | "minor" | "patch";
export type Message13 = string;
/**
 * the Copilot proposals accepted since the version before, and by whom
 */
export type CopilotChanges1 = CopilotChange[];
export type CreatedAt32 = string;
export type CreatedBy3 = string | null;
export type Id59 = string;
export type IrHash5 = string;
export type Message14 = string;
export type Semver2 = string;
export type WorkspaceCommit1 = string | null;
export type Model12 = string | null;
export type Name59 = string;
export type SystemPrompt1 = string;
export type Temperature1 = number | null;
/**
 * become Python tool blocks
 *
 * @maxItems 20
 */
export type Tools4 =
  | []
  | [string]
  | [string, string]
  | [string, string, string]
  | [string, string, string, string]
  | [string, string, string, string, string]
  | [string, string, string, string, string, string]
  | [string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [string, string, string, string, string, string, string, string, string, string, string, string, string, string]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ]
  | [
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string,
      string
    ];
export type CreatedAt33 = string;
export type Diagnostics5 = Diagnostic[];
export type Id60 = string;
export type IrHash6 = string;
export type Name60 = string;
export type ProjectId6 = string;
export type UpdatedAt11 = string;
export type Version13 = number;
export type Id61 = string;
export type Name61 = string;
export type UpdatedAt12 = string;
export type Version14 = number;
export type Items3 = WorkflowSummary[];
export type NextCursor1 = string | null;
export type Name62 = string;
export type Id62 = string;
export type IsDefault = boolean;
export type Name63 = string;
/**
 * default: the latest version
 */
export type Version15 = number | null;
export type Id63 = string;
export type Kind13 = string;
/**
 * what the content is, read from its first bytes
 */
export type MediaType = string;
export type Name64 = string;
export type Size3 = number;
/**
 * the commit message
 */
export type Message15 = string | null;
/**
 * replace changes made on the branch since the last sync
 */
export type Overwrite1 = boolean;
/**
 * a version's semver; the draft when left out
 */
export type Version16 = string | null;
export type Error10 = string | null;
export type Ok4 = boolean;
export type Reply1 = string | null;
export type Detail4 = string;
export type Ok5 = boolean;

export interface ApiSchemas {
  AddNode?: AddNode;
  AddResource?: AddResource;
  AddToDataset?: AddToDataset;
  AgentCodeOut?: AgentCodeOut;
  AgentFrameworkOut?: AgentFrameworkOut;
  AgentSpecExport?: AgentSpecExport;
  AgentSpecImport?: AgentSpecImport;
  AgentSpecImported?: AgentSpecImported;
  AlertOut?: AlertOut;
  AnnotateIn?: AnnotateIn;
  AnnotationOut?: AnnotationOut;
  ApiResult?: ApiResult;
  ApiResumeIn?: ApiResumeIn;
  ApiRunIn?: ApiRunIn;
  AuditOut?: AuditOut;
  AuthConfig?: AuthConfig;
  BlastRadius?: BlastRadius;
  Body_upload_documents_v1_knowledge__kb_id__documents_post?: BodyUploadDocumentsV1Knowledge_KbId_DocumentsPost;
  Body_upload_file_v1_projects__project_id__uploads_post?: BodyUploadFileV1Projects_ProjectId_UploadsPost;
  Breakpoints?: Breakpoints;
  BridgeOut?: BridgeOut;
  BridgesOut?: BridgesOut;
  BucketOut?: BucketOut;
  CachePolicySpec?: CachePolicySpec;
  Capability?: Capability;
  CaseResult?: CaseResult;
  CatalogOut?: CatalogOut;
  Channel?: Channel;
  ChatIn?: ChatIn;
  ChatModelIn?: ChatModelIn;
  ChatOut?: ChatOut;
  Check?: Check;
  ChunkOut?: ChunkOut;
  Chunking?: Chunking;
  CodeHelpIn?: CodeHelpIn;
  CodeHelpOut?: CodeHelpOut;
  CommandBatch?: CommandBatch;
  CommandResult?: CommandResult;
  Connect?: Connect;
  ConnectorSecret?: ConnectorSecret;
  ContextField?: ContextField;
  ContractChange?: ContractChange;
  ContractOut?: ContractOut;
  ConversationOut?: ConversationOut;
  CopilotChange?: CopilotChange;
  CopilotSettings?: CopilotSettings;
  CopilotThreadOut?: CopilotThreadOut;
  CopilotThreadPatch?: CopilotThreadPatch;
  CostAlertOut?: CostAlertOut;
  CreateWorkflow?: CreateWorkflow;
  Criterion?: Criterion;
  DatasetDetail?: DatasetDetail;
  DatasetIn?: DatasetIn;
  DatasetOut?: DatasetOut;
  DatasetPatch?: DatasetPatch;
  DatasetVersionOut?: DatasetVersionOut;
  DebugOptions?: DebugOptions;
  Decision?: Decision;
  DefaultModel?: DefaultModel;
  DeploymentOut?: DeploymentOut;
  DesignGapOut?: DesignGapOut;
  Diagnostic?: Diagnostic;
  DiffOut?: DiffOut;
  Disconnect?: Disconnect;
  DocumentOut?: DocumentOut;
  DuplicateIn?: DuplicateIn;
  Edge?: Edge;
  ElementChange?: ElementChange;
  ElementDiff?: ElementDiff;
  Embedding?: Embedding;
  EnabledIn?: EnabledIn;
  Endpoint?: Endpoint;
  EvaluatorIn?: EvaluatorIn;
  EvaluatorKindOut?: EvaluatorKindOut;
  EvaluatorOut?: EvaluatorOut;
  EvaluatorPatch?: EvaluatorPatch;
  EvaluatorSummary?: EvaluatorSummary;
  EventIn?: EventIn;
  EventLogOut?: EventLogOut;
  Example?: Example;
  ExamplePatch?: ExamplePatch;
  ExamplesIn?: ExamplesIn;
  Expect?: Expect;
  ExperimentIn?: ExperimentIn;
  ExperimentOut?: ExperimentOut;
  ExperimentResultOut?: ExperimentResultOut;
  ExportPlanOut?: ExportPlanOut;
  ExportTargetOut?: ExportTargetOut;
  ExpressionCheck?: ExpressionCheck;
  ExpressionResult?: ExpressionResult;
  FeedbackIn?: FeedbackIn;
  FieldChange?: FieldChange;
  FileContent?: FileContent;
  FileMove?: FileMove;
  FileSave?: FileSave;
  FilesOut?: FilesOut;
  FilterIn?: FilterIn;
  ForkRequest?: ForkRequest;
  FrameworkOut?: FrameworkOut;
  FromRunIn?: FromRunIn;
  FromRunsIn?: FromRunsIn;
  FromTestCasesIn?: FromTestCasesIn;
  GapOut?: GapOut;
  GateIn?: GateIn;
  GateOut?: GateOut;
  GitHubPushIn?: GitHubPushIn;
  GitHubPushOut?: GitHubPushOut;
  GitLinkIn?: GitLinkIn;
  GitLinkOut?: GitLinkOut;
  GitSyncOut?: GitSyncOut;
  GuidePage?: GuidePage;
  GuidePageRef?: GuidePageRef;
  HTTPValidationError?: HTTPValidationError;
  HeaderRule?: HeaderRule;
  ImportFile?: ImportFile;
  ImportIn?: ImportIn;
  Imported?: Imported;
  InlineRequest?: InlineRequest;
  InputShape?: InputShape;
  InterruptOut?: InterruptOut;
  InvitationOut?: InvitationOut;
  ItemDetail?: ItemDetail;
  ItemOut?: ItemOut;
  ItemsIn?: ItemsIn;
  KeyOut?: KeyOut;
  KitOut?: KitOut;
  KnowledgeBaseIn?: KnowledgeBaseIn;
  KnowledgeBaseOut?: KnowledgeBaseOut;
  KnowledgeBasePatch?: KnowledgeBasePatch;
  KnowledgeDefaults?: KnowledgeDefaults;
  LangSmithLink?: LangSmithLink;
  LayoutUpdate?: LayoutUpdate;
  LintItem?: LintItem;
  Mapping?: Mapping;
  McpConnector?: McpConnector;
  McpInspectRequest?: McpInspectRequest;
  McpToolInfo?: McpToolInfo;
  MeOut?: MeOut;
  MemberIn?: MemberIn;
  MemberOut?: MemberOut;
  MemoryItem?: MemoryItem;
  Message?: Message2;
  MessageIn?: MessageIn;
  ModelDefaultsIn?: ModelDefaultsIn;
  ModelDefaultsOut?: ModelDefaultsOut;
  ModelInfo?: ModelInfo;
  ModelInfoOut?: ModelInfoOut;
  ModelOption?: ModelOption;
  NamespaceOut?: NamespaceOut;
  NewScript?: NewScript;
  Node?: Node;
  NodeBlast?: NodeBlast;
  NodeManifest?: NodeManifest;
  NodePolicies?: NodePolicies;
  NotOfferedOut?: NotOfferedOut;
  Observation?: Observation;
  OnlineIn?: OnlineIn;
  OnlineOut?: OnlineOut;
  OnlinePatch?: OnlinePatch;
  OrgLimits?: OrgLimits;
  OrgLimitsOut?: OrgLimitsOut;
  OrgSettings?: OrgSettings;
  OrgSettingsOut?: OrgSettingsOut;
  PolicyIn?: PolicyIn;
  PolicyOut?: PolicyOut;
  PortSpec?: PortSpec;
  PortabilityOut?: PortabilityOut;
  Preferences?: Preferences;
  Progress?: Progress;
  ProjectIn?: ProjectIn;
  ProjectOut?: ProjectOut;
  ProjectRenameIn?: ProjectRenameIn;
  ProjectWorkspaceIn?: ProjectWorkspaceIn;
  ProposalDecision?: ProposalDecision;
  ProviderInfo?: ProviderInfo;
  PublishIn?: PublishIn;
  PublishOut?: PublishOut;
  PullIn?: PullIn;
  PullOut?: PullOut;
  PushOut?: PushOut;
  QueueIn?: QueueIn;
  QueueOut?: QueueOut;
  QueuePatch?: QueuePatch;
  RecrawlIn?: RecrawlIn;
  RecrawlOut?: RecrawlOut;
  RecrawlResult?: RecrawlResult;
  RefactorOut?: RefactorOut;
  RemoveNode?: RemoveNode;
  RemoveResource?: RemoveResource;
  RenameNode?: RenameNode;
  ReplaceIR?: ReplaceIR;
  ReplaceNode?: ReplaceNode;
  ReplaceResource?: ReplaceResource;
  ReplaceSettings?: ReplaceSettings;
  Resolved?: Resolved1;
  Resource?: Resource;
  ResumeRequest?: ResumeRequest;
  RetryPolicySpec?: RetryPolicySpec;
  Rules?: Rules;
  RunOut?: RunOut;
  RunRequest?: RunRequest;
  SandboxOut?: SandboxOut;
  SandboxPolicy?: SandboxPolicy;
  SavedFile?: SavedFile;
  Score?: Score;
  ScoreOut?: ScoreOut;
  ScoredRunOut?: ScoredRunOut;
  SearchIn?: SearchIn;
  SearchOut?: SearchOut;
  SearchResult?: SearchResult;
  SecretIn?: SecretIn;
  SecretOut?: SecretOut;
  ServerModelsIn?: ServerModelsIn;
  ServerModelsOut?: ServerModelsOut;
  ServerSetting?: ServerSetting;
  SetChannels?: SetChannels;
  SetContext?: SetContext;
  SettingsOut?: SettingsOut;
  SetupIn?: SetupIn;
  SetupOut?: SetupOut;
  ShareFeedbackIn?: ShareFeedbackIn;
  ShareInfo?: ShareInfo;
  ShareOut?: ShareOut;
  ShareResumeIn?: ShareResumeIn;
  ShareRunIn?: ShareRunIn;
  ShareSettingsIn?: ShareSettingsIn;
  Simulation?: Simulation;
  SitemapIn?: SitemapIn;
  SpendOut?: SpendOut;
  SpendPeriod?: SpendPeriod;
  SpendPerson?: SpendPerson;
  SpendProject?: SpendProject;
  SpendTotal?: SpendTotal;
  SpendWorkflow?: SpendWorkflow;
  SpendingOut?: SpendingOut;
  StateEdit?: StateEdit;
  StopOut?: StopOut;
  SubgraphRequest?: SubgraphRequest;
  Surface?: Surface;
  TargetIn?: TargetIn;
  TargetOut?: TargetOut;
  TargetSummary?: TargetSummary;
  TemplateInfo?: TemplateInfo;
  TestCaseIn?: TestCaseIn;
  TestCaseOut?: TestCaseOut;
  TestRunIn?: TestRunIn;
  TestRunOut?: TestRunOut;
  TextIn?: TextIn;
  ThreadOut?: ThreadOut;
  ThreadPage?: ThreadPage;
  TimeoutPolicySpec?: TimeoutPolicySpec;
  ToolUse?: ToolUse;
  TraceExport?: TraceExport;
  TraceExportOut?: TraceExportOut;
  TriggerCreated?: TriggerCreated;
  TriggerIn?: TriggerIn;
  TriggerOut?: TriggerOut;
  UpdateMeta?: UpdateMeta;
  UpdateNode?: UpdateNode;
  UpdateResource?: UpdateResource;
  UpdateSettings?: UpdateSettings;
  UrlIn?: UrlIn;
  ValidationError?: ValidationError;
  VersionDetail?: VersionDetail;
  VersionIn?: VersionIn;
  VersionOut?: VersionOut;
  WorkflowIn?: WorkflowIn;
  WorkflowOut?: WorkflowOut;
  WorkflowPage?: WorkflowPage;
  WorkflowPatch?: WorkflowPatch;
  WorkflowSummary?: WorkflowSummary;
  WorkspaceOut?: WorkspaceOut;
  agentcanvas__api__v1__datasets__PushIn?: PushIn;
  agentcanvas__api__v1__files__FileOut?: FileOut1;
  agentcanvas__api__v1__gitsync__PushIn?: PushIn1;
  agentcanvas__api__v1__setup__TestOut?: TestOut;
  agentcanvas__api__v1__traces__TestOut?: TestOut1;
  agentcanvas__api__v1__workspace__FileOut?: FileOut;
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
  engine?: Engine;
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
 * via the `definition` "AddToDataset".
 */
export interface AddToDataset {
  dataset_id: DatasetId;
  reference: Reference;
}
/**
 * the corrected output: channel -> expected value
 */
export interface Reference {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AgentCodeOut".
 */
export interface AgentCodeOut {
  code: Code;
  export_problems: ExportProblems;
  file?: File;
  framework: Framework;
  gaps?: Gaps;
  label: Label2;
  problems: Problems;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DesignGapOut".
 */
export interface DesignGapOut {
  blocks: Blocks;
  text: Text;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AgentFrameworkOut".
 */
export interface AgentFrameworkOut {
  api: Api;
  hosted?: Hosted;
  id: Id2;
  installed: Installed;
  label: Label3;
  own_env: OwnEnv;
  packages: Packages;
  structured_output: StructuredOutput;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AgentSpecExport".
 */
export interface AgentSpecExport {
  report: Report;
  spec: Spec;
}
export interface Spec {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AgentSpecImport".
 */
export interface AgentSpecImport {
  name?: Name2;
  spec: Spec1;
}
export interface Spec1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AgentSpecImported".
 */
export interface AgentSpecImported {
  created_at: CreatedAt;
  diagnostics: Diagnostics;
  id: Id3;
  ir: Ir;
  ir_hash: IrHash;
  /**
   * created from a template: the files, dataset, evaluators, tests and schedule it added
   */
  kit?: KitOut | null;
  layout: Layout;
  name: Name3;
  project_id: ProjectId;
  report: Report1;
  updated_at: UpdatedAt;
  version: Version;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Diagnostic".
 */
export interface Diagnostic {
  code: Code1;
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
/**
 * What creating the workflow added beside it.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KitOut".
 */
export interface KitOut {
  dataset_id?: DatasetId1;
  evaluators?: Evaluators;
  files_kept?: FilesKept;
  files_written?: FilesWritten;
  tests?: Tests;
  triggers?: Triggers;
}
export interface Layout {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AlertOut".
 */
export interface AlertOut {
  baseline: Baseline;
  created_at: CreatedAt1;
  deployment_id: DeploymentId;
  evaluator: Evaluator;
  id: Id4;
  kind: Kind;
  message: Message1;
  resolved_at: ResolvedAt;
  value: Value;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AnnotateIn".
 */
export interface AnnotateIn {
  add_to_dataset?: AddToDataset | null;
  comment?: Comment;
  scores: Scores;
}
/**
 * criterion -> points (1..scale)
 */
export interface Scores {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AnnotationOut".
 */
export interface AnnotationOut {
  comment: Comment1;
  created_at: CreatedAt2;
  reviewer: Reviewer;
  scores: Scores1;
}
export interface Scores1 {
  [k: string]: number;
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
  value?: Value1;
}
export interface Value1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ApiRunIn".
 */
export interface ApiRunIn {
  context?: Context;
  input?: Input;
  metadata?: Metadata1;
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
 * labels for this run; online evaluators can filter on them
 */
export interface Metadata1 {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "AuditOut".
 */
export interface AuditOut {
  action: Action;
  actor_email: ActorEmail;
  actor_id: ActorId;
  created_at: CreatedAt3;
  detail: Detail;
  id: Id5;
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
  portal_url?: PortalUrl;
  scopes?: Scopes;
  tenant_param?: TenantParam;
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
  name: Name4;
  node_id: NodeId1;
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
 * via the `definition` "Body_upload_documents_v1_knowledge__kb_id__documents_post".
 */
export interface BodyUploadDocumentsV1Knowledge_KbId_DocumentsPost {
  files: Files;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Body_upload_file_v1_projects__project_id__uploads_post".
 */
export interface BodyUploadFileV1Projects_ProjectId_UploadsPost {
  file: File1;
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
 * via the `definition` "BridgeOut".
 */
export interface BridgeOut {
  gaps: Gaps1;
  id: Id6;
  label: Label4;
  packages: Packages1;
  preview?: Preview;
  runs: Runs;
  summary: Summary1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GapOut".
 */
export interface GapOut {
  blocks: Blocks1;
  text: Text1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "BridgesOut".
 */
export interface BridgesOut {
  not_offered: NotOffered;
  targets: Targets;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NotOfferedOut".
 */
export interface NotOfferedOut {
  label: Label5;
  why: Why;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "BucketOut".
 */
export interface BucketOut {
  evaluator: Evaluator1;
  judge_cost_usd: JudgeCostUsd;
  mean: Mean;
  pass_rate: PassRate;
  runs: Runs1;
  scored: Scored;
  start: Start;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CaseResult".
 */
export interface CaseResult {
  duration_ms?: DurationMs;
  failures?: Failures;
  id: Id7;
  name: Name5;
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
  problems: Problems1;
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
  name: Name6;
  reducer?: Reducer;
  storage?: Storage;
  type?: Type2;
}
export interface Default {
  [k: string]: unknown;
}
/**
 * JSON Schema of the value; `{"type": "object", "format": "file"}` holds a file a person attached (a reference to an upload, see agentcanvas/ir/files.py)
 */
export interface Type2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ChatIn".
 */
export interface ChatIn {
  messages: Messages;
  model?: Model;
  system_prompt?: SystemPrompt;
  temperature?: Temperature;
  tools?: Tools1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Message".
 */
export interface Message2 {
  content: Content;
  role: Role;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ChatModelIn".
 */
export interface ChatModelIn {
  model?: Model1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ChatOut".
 */
export interface ChatOut {
  model: Model2;
  reply: Reply;
  tools?: Tools2;
  usage?: Usage;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ToolUse".
 */
export interface ToolUse {
  args: Args;
  name: Name7;
  result: Result;
}
export interface Args {
  [k: string]: unknown;
}
export interface Usage {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Check".
 */
export interface Check {
  detail?: Detail1;
  items?: Items;
  name: Name8;
  ok: Ok;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ChunkOut".
 */
export interface ChunkOut {
  heading: Heading;
  id: Id8;
  ordinal: Ordinal;
  page: Page;
  text: Text2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Chunking".
 */
export interface Chunking {
  overlap?: Overlap;
  size?: Size;
  strategy?: Strategy;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CodeHelpIn".
 */
export interface CodeHelpIn {
  action: Action1;
  content?: Content1;
  instruction?: Instruction;
  path: Path1;
  problems?: Problems2;
  selection?: Selection;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CodeHelpOut".
 */
export interface CodeHelpOut {
  code?: Code2;
  model: Model3;
  text: Text3;
  usage?: Usage1;
}
export interface Usage1 {
  [k: string]: number;
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
  id: Id9;
  op?: Op2;
}
/**
 * JSON merge patch on a node (label, config, policies, disabled, metadata, name).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateNode".
 */
export interface UpdateNode {
  id: Id10;
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
  id: Id11;
  name: Name9;
  op?: Op5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RemoveResource".
 */
export interface RemoveResource {
  id: Id12;
  op?: Op6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateResource".
 */
export interface UpdateResource {
  id: Id13;
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
  id: Id14;
  kind: Kind1;
  label?: Label6;
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
  id: Id15;
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
  name: Name10;
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
 * Change name / description / metadata / framework of the workflow.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UpdateMeta".
 */
export interface UpdateMeta {
  description?: Description2;
  framework?: Framework1;
  metadata?: Metadata2;
  name?: Name11;
  op?: Op15;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CommandResult".
 */
export interface CommandResult {
  created_at: CreatedAt4;
  diagnostics: Diagnostics1;
  id: Id16;
  inverse: Inverse;
  ir: Ir1;
  ir_hash: IrHash1;
  /**
   * created from a template: the files, dataset, evaluators, tests and schedule it added
   */
  kit?: KitOut | null;
  layout: Layout1;
  name: Name12;
  project_id: ProjectId1;
  updated_at: UpdatedAt1;
  version: Version1;
}
export interface Ir1 {
  [k: string]: unknown;
}
export interface Layout1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ConnectorSecret".
 */
export interface ConnectorSecret {
  name: Name13;
  what?: What;
  where?: Where;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ContractChange".
 */
export interface ContractChange {
  field: Field;
  message: Message3;
  severity: Severity1;
  where: Where1;
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
  kind: Kind2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ConversationOut".
 */
export interface ConversationOut {
  brief: Brief;
  chosen_model?: ChosenModel;
  cost_usd?: CostUsd;
  enabled: Enabled;
  id: Id17;
  messages: Messages1;
  model: ModelInfoOut;
  pack_tokens: PackTokens;
  pending?: Pending;
  proposals: Proposals;
  token_cap: TokenCap;
  usage: Usage2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelInfoOut".
 */
export interface ModelInfoOut {
  model?: Model4;
  origin?: Origin;
  problem?: Problem;
}
export interface Usage2 {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CopilotChange".
 */
export interface CopilotChange {
  accepted_at: AcceptedAt;
  accepted_by: AcceptedBy;
  accepted_by_email?: AcceptedByEmail;
  changes: Changes;
  summary: Summary2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CopilotSettings".
 */
export interface CopilotSettings {
  api_key?: ApiKey;
  ask_model?: AskModel;
  base_url?: BaseUrl;
  enabled?: Enabled1;
  model?: Model5;
  notes?: Notes;
  privacy?: Privacy;
  source?: Source2;
  token_cap?: TokenCap1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CopilotThreadOut".
 */
export interface CopilotThreadOut {
  created_at: CreatedAt5;
  id: Id18;
  messages: Messages2;
  open: Open;
  title: Title;
  updated_at: UpdatedAt2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CopilotThreadPatch".
 */
export interface CopilotThreadPatch {
  title: Title1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CostAlertOut".
 */
export interface CostAlertOut {
  created_at: CreatedAt6;
  id: Id19;
  limit_usd: LimitUsd;
  message: Message4;
  period: Period;
  period_start: PeriodStart;
  run_id: RunId2;
  share: Share;
  spent_usd: SpentUsd;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "CreateWorkflow".
 */
export interface CreateWorkflow {
  name?: Name14;
  template?: Template;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Criterion".
 */
export interface Criterion {
  description?: Description3;
  name: Name15;
  pass_at?: PassAt;
  scale?: Scale;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetDetail".
 */
export interface DatasetDetail {
  created_at: CreatedAt7;
  description: Description4;
  examples: Examples;
  id: Id20;
  items: Items1;
  /**
   * the LangSmith dataset it is synced with
   */
  langsmith?: LangSmithLink | null;
  name: Name17;
  splits: Splits;
  updated_at: UpdatedAt3;
  version: Version2;
  version_id: VersionId;
  workflow_id: WorkflowId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Example".
 */
export interface Example {
  context?: Context2;
  id?: Id21;
  input?: Input2;
  metadata?: Metadata3;
  reference?: Reference1;
  split?: Split;
  trajectory?: Trajectory;
}
export interface Input2 {
  [k: string]: unknown;
}
export interface Metadata3 {
  [k: string]: unknown;
}
/**
 * output channel -> expected value
 */
export interface Reference1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "LangSmithLink".
 */
export interface LangSmithLink {
  dataset_id: DatasetId2;
  name: Name16;
  pulled_version?: PulledVersion;
  pushed_version?: PushedVersion;
  synced_at: SyncedAt;
  url: Url;
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
  description?: Description5;
  name: Name18;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetOut".
 */
export interface DatasetOut {
  created_at: CreatedAt8;
  description: Description6;
  examples: Examples1;
  id: Id22;
  /**
   * the LangSmith dataset it is synced with
   */
  langsmith?: LangSmithLink | null;
  name: Name19;
  splits: Splits1;
  updated_at: UpdatedAt4;
  version: Version3;
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
  description?: Description7;
  name?: Name20;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DatasetVersionOut".
 */
export interface DatasetVersionOut {
  created_at: CreatedAt9;
  created_by: CreatedBy;
  examples: Examples2;
  id: Id23;
  message: Message5;
  number: Number;
}
/**
 * Debug-run aids; never part of the workflow or its exported code.
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
  value: Value2;
}
export interface Value2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DefaultModel".
 */
export interface DefaultModel {
  api_key?: ApiKey1;
  base_url?: BaseUrl1;
  model: Model6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DeploymentOut".
 */
export interface DeploymentOut {
  active: Active;
  created_at: CreatedAt10;
  endpoint: Endpoint1;
  environment: Environment;
  id: Id24;
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
  summary: Summary3;
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
  id: Id25;
  name?: Name21;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FieldChange".
 */
export interface FieldChange {
  after?: After1;
  before?: Before1;
  path: Path2;
}
export interface After1 {
  [k: string]: unknown;
}
export interface Before1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DocumentOut".
 */
export interface DocumentOut {
  characters: Characters;
  chunks: Chunks;
  created_at: CreatedAt11;
  id: Id26;
  name: Name22;
  source: Source3;
  url: Url1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "DuplicateIn".
 */
export interface DuplicateIn {
  name?: Name23;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Embedding".
 */
export interface Embedding {
  api_key?: ApiKey2;
  base_url?: BaseUrl2;
  model?: Model7;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EnabledIn".
 */
export interface EnabledIn {
  enabled: Enabled2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorIn".
 */
export interface EvaluatorIn {
  config?: Config2;
  kind: Kind3;
  name: Name24;
  threshold?: Threshold;
}
export interface Config2 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorKindOut".
 */
export interface EvaluatorKindOut {
  config_schema: ConfigSchema;
  default_threshold: DefaultThreshold;
  description: Description8;
  experiments_only: ExperimentsOnly;
  kind: Kind4;
  label: Label7;
}
/**
 * JSON Schema of the kind's config, for a form
 */
export interface ConfigSchema {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorOut".
 */
export interface EvaluatorOut {
  config?: Config3;
  created_at: CreatedAt12;
  effective_threshold: EffectiveThreshold;
  id: Id27;
  kind: Kind5;
  name: Name25;
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
  name?: Name26;
  threshold?: Threshold2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "EvaluatorSummary".
 */
export interface EvaluatorSummary {
  ci?: Ci;
  evaluator: Evaluator2;
  mean: Mean1;
  pass_rate: PassRate1;
  scored: Scored1;
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
  created_at: CreatedAt13;
  data: Data1;
  id: Id28;
  results: Results;
  source: Source4;
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
  metadata?: Metadata4;
  reference?: Reference2;
  split?: Split1;
  trajectory?: Trajectory1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExamplesIn".
 */
export interface ExamplesIn {
  examples: Examples3;
  message?: Message6;
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
 * via the `definition` "ExperimentIn".
 */
export interface ExperimentIn {
  concurrency?: Concurrency;
  dataset_id: DatasetId3;
  dataset_version?: DatasetVersion;
  evaluator_ids?: EvaluatorIds;
  name?: Name27;
  /**
   * chat workflows: a simulated user holds a conversation per example
   */
  simulation?: Simulation | null;
  split?: Split2;
  targets?: Targets1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Simulation".
 */
export interface Simulation {
  goal?: Goal;
  max_turns?: MaxTurns;
  model?: Model8;
  persona: Persona;
  stop_phrase?: StopPhrase;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TargetIn".
 */
export interface TargetIn {
  framework?: Framework2;
  label?: Label8;
  overrides?: Overrides;
  version?: Version4;
}
/**
 * resource or step (id or name) -> config values to replace
 */
export interface Overrides {
  [k: string]: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExperimentOut".
 */
export interface ExperimentOut {
  concurrency: Concurrency1;
  created_at: CreatedAt14;
  dataset_id: DatasetId4;
  dataset_version: DatasetVersion1;
  ended_at: EndedAt;
  error: Error1;
  evaluators: Evaluators1;
  id: Id29;
  name: Name28;
  pairwise?: Pairwise;
  simulation?: Simulation | null;
  split: Split3;
  status: Status4;
  summary: Summary4;
  targets: Targets2;
  workflow_id: WorkflowId3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TargetSummary".
 */
export interface TargetSummary {
  completed: Completed;
  cost_usd: CostUsd1;
  errors: Errors;
  evaluators: Evaluators2;
  examples: Examples4;
  judge_cost_usd?: JudgeCostUsd1;
  judge_unpriced?: JudgeUnpriced;
  label: Label9;
  latency_p50_ms: LatencyP50Ms;
  latency_p95_ms: LatencyP95Ms;
  pass_rate: PassRate2;
  target: Target1;
  tokens: Tokens;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TargetOut".
 */
export interface TargetOut {
  framework?: Framework3;
  label: Label10;
  overrides: Overrides1;
  version: Version5;
}
export interface Overrides1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExperimentResultOut".
 */
export interface ExperimentResultOut {
  cost_usd: CostUsd2;
  error: Error2;
  example_id: ExampleId;
  id: Id30;
  latency_ms: LatencyMs;
  output: Output2;
  position: Position;
  run_id: RunId3;
  run_status: RunStatus;
  scores: Scores2;
  status: Status5;
  target: Target2;
  thread_id: ThreadId3;
  tokens_in: TokensIn;
  tokens_out: TokensOut;
  trajectory: Trajectory2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Score".
 */
export interface Score {
  comment?: Comment2;
  evaluator: Evaluator3;
  passed: Passed1;
  score: Score1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExportPlanOut".
 */
export interface ExportPlanOut {
  prerequisites: Prerequisites;
  targets: Targets3;
}
/**
 * what the chosen target needs: tools, keys, hosts, database
 */
export interface Prerequisites {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExportTargetOut".
 */
export interface ExportTargetOut {
  id: Id31;
  label: Label11;
  summary: Summary5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionCheck".
 */
export interface ExpressionCheck {
  kind: Kind6;
  source: Source5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ExpressionResult".
 */
export interface ExpressionResult {
  error?: Error3;
  ok: Ok1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FeedbackIn".
 */
export interface FeedbackIn {
  comment?: Comment3;
  rating: Rating;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileContent".
 */
export interface FileContent {
  content: Content2;
  diagnostics?: Diagnostics2;
  path: Path3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "LintItem".
 */
export interface LintItem {
  code: Code3;
  column: Column;
  end_column: EndColumn;
  end_line: EndLine;
  line: Line;
  message: Message7;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileMove".
 */
export interface FileMove {
  path: Path4;
  to: To;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FileSave".
 */
export interface FileSave {
  content: Content3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FilesOut".
 */
export interface FilesOut {
  files: Files1;
  folder?: Folder;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__workspace__FileOut".
 */
export interface FileOut {
  modified: Modified;
  path: Path5;
  size: Size1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FilterIn".
 */
export interface FilterIn {
  metadata?: Metadata5;
  status?: Status6;
}
/**
 * only runs the caller labelled so
 */
export interface Metadata5 {
  [k: string]: string;
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
 * via the `definition` "FrameworkOut".
 */
export interface FrameworkOut {
  abilities?: Abilities;
  description?: Description9;
  framework: Framework4;
  id: Id32;
  label: Label12;
  languages?: Languages;
  nodes?: Nodes2;
  notes?: Notes1;
  ready?: Ready;
  setup?: Setup;
  tier: Tier;
  version: Version6;
}
export interface Nodes2 {
  [k: string]: "native" | "emulated" | "unsupported";
}
export interface Notes1 {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "FromRunIn".
 */
export interface FromRunIn {
  name: Name29;
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
 * via the `definition` "GateIn".
 */
export interface GateIn {
  dataset_id: DatasetId5;
  dataset_version?: DatasetVersion2;
  max_regression?: MaxRegression;
  min_scores?: MinScores;
}
/**
 * evaluator name -> minimum mean score (none: each evaluator's threshold)
 */
export interface MinScores {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GateOut".
 */
export interface GateOut {
  dataset_id: DatasetId6;
  dataset_version?: DatasetVersion3;
  environment: Environment1;
  evaluators: Evaluators3;
  id: Id33;
  max_regression?: MaxRegression1;
  min_scores?: MinScores1;
  updated_at: UpdatedAt5;
  workflow_id: WorkflowId4;
}
/**
 * evaluator name -> minimum mean score (none: each evaluator's threshold)
 */
export interface MinScores1 {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GitHubPushIn".
 */
export interface GitHubPushIn {
  branch: Branch;
  folder?: Folder1;
  message?: Message8;
  repo: Repo;
  target?: Target3;
  token_secret?: TokenSecret;
  version?: Version7;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GitHubPushOut".
 */
export interface GitHubPushOut {
  branch: Branch1;
  commit: Commit;
  created_branch: CreatedBranch;
  files: Files2;
  url: Url2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GitLinkIn".
 */
export interface GitLinkIn {
  auto?: Auto;
  branch: Branch2;
  folder?: Folder2;
  repo: Repo1;
  target?: Target4;
  token_secret?: TokenSecret1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GitLinkOut".
 */
export interface GitLinkOut {
  auto: Auto1;
  branch: Branch3;
  folder: Folder3;
  last_error?: LastError;
  last_synced_at?: LastSyncedAt;
  last_version?: LastVersion;
  pr_number?: PrNumber;
  pr_url?: PrUrl;
  repo: Repo2;
  target: Target5;
  token_secret: TokenSecret2;
}
/**
 * What opening the pull request after a publish or a saved version did (`error`: it did not, and why).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GitSyncOut".
 */
export interface GitSyncOut {
  error?: Error4;
  pr_number?: PrNumber1;
  pr_url?: PrUrl1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GuidePage".
 */
export interface GuidePage {
  markdown: Markdown;
  page: Page1;
  title: Title2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "GuidePageRef".
 */
export interface GuidePageRef {
  page: Page2;
  title: Title3;
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
  msg: Message9;
  type: ErrorType;
}
export interface Context4 {}
export interface Input4 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "HeaderRule".
 */
export interface HeaderRule {
  headers: Headers;
  hosts: Hosts;
  name: Name30;
  paths?: Paths;
}
/**
 * values may use {{ secret.NAME }}
 */
export interface Headers {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ImportFile".
 */
export interface ImportFile {
  code?: Code4;
  file?: File2;
  name?: Name31;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ImportIn".
 */
export interface ImportIn {
  content: Content4;
  format: Format;
  /**
   * CSV only: columns -> fields
   */
  mapping?: Mapping | null;
  split?: Split5;
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
  reference?: Reference3;
  split?: Split4;
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
export interface Reference3 {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Imported".
 */
export interface Imported {
  created_at: CreatedAt15;
  diagnostics: Diagnostics3;
  format: Format1;
  id: Id34;
  ir: Ir2;
  ir_hash: IrHash2;
  /**
   * created from a template: the files, dataset, evaluators, tests and schedule it added
   */
  kit?: KitOut | null;
  layout: Layout2;
  name: Name32;
  project_id: ProjectId2;
  report: Report2;
  updated_at: UpdatedAt6;
  version: Version8;
}
export interface Ir2 {
  [k: string]: unknown;
}
export interface Layout2 {
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
 * via the `definition` "InputShape".
 */
export interface InputShape {
  chat?: Chat;
  field?: Field1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "InterruptOut".
 */
export interface InterruptOut {
  canvas_node_id: CanvasNodeId;
  created_at: CreatedAt16;
  decided_at: DecidedAt;
  decision: Decision1;
  id: Id35;
  kind: Kind7;
  node_label?: NodeLabel;
  payload: Payload;
  project_id?: ProjectId3;
  project_name?: ProjectName;
  run_id: RunId4;
  status: Status7;
  thread_id: ThreadId4;
  workflow_id?: WorkflowId5;
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
  role: Role1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ItemDetail".
 */
export interface ItemDetail {
  annotations: Annotations;
  created_at: CreatedAt17;
  error: Error5;
  id: Id36;
  input: Input6;
  output: Output3;
  queue_id: QueueId;
  reason: Reason;
  reviewed_by_me: ReviewedByMe;
  reviews: Reviews;
  run_id: RunId5;
  run_status: RunStatus1;
  scores: Scores3;
  status: Status8;
  thread_id: ThreadId6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ScoreOut".
 */
export interface ScoreOut {
  comment: Comment4;
  created_at: CreatedAt18;
  created_by: CreatedBy1;
  evaluator: Evaluator4;
  id: Id37;
  judge_cost_usd: JudgeCostUsd2;
  judge_tokens: JudgeTokens;
  passed: Passed2;
  run_id: RunId6;
  score: Score2;
  source: Source6;
  thread_id: ThreadId5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ItemOut".
 */
export interface ItemOut {
  created_at: CreatedAt19;
  id: Id38;
  queue_id: QueueId1;
  reason: Reason1;
  reviewed_by_me: ReviewedByMe1;
  reviews: Reviews1;
  run_id: RunId7;
  status: Status9;
  thread_id: ThreadId7;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ItemsIn".
 */
export interface ItemsIn {
  run_ids: RunIds1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KeyOut".
 */
export interface KeyOut {
  api_key: ApiKey3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KnowledgeBaseIn".
 */
export interface KnowledgeBaseIn {
  chunking?: Chunking | null;
  description?: Description10;
  embedding?: Embedding | null;
  name?: Name33;
  sample?: Sample;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KnowledgeBaseOut".
 */
export interface KnowledgeBaseOut {
  chunking: Chunking;
  chunks: Chunks1;
  created_at: CreatedAt20;
  description: Description11;
  documents: Documents;
  embedding: Embedding1;
  id: Id39;
  name: Name34;
  project_id: ProjectId4;
  recrawl: RecrawlOut;
  stale: Stale;
  updated_at: UpdatedAt7;
  version: Version9;
}
export interface Embedding1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RecrawlOut".
 */
export interface RecrawlOut {
  every_hours: EveryHours;
  last: RecrawlResult | null;
  sitemaps: Sitemaps;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RecrawlResult".
 */
export interface RecrawlResult {
  added: Added1;
  at: At;
  failed: Failed;
  removed: Removed1;
  unchanged: Unchanged;
  updated: Updated;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KnowledgeBasePatch".
 */
export interface KnowledgeBasePatch {
  chunking?: Chunking | null;
  description?: Description12;
  embedding?: Embedding | null;
  name?: Name35;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "KnowledgeDefaults".
 */
export interface KnowledgeDefaults {
  chunking?: Chunking1;
}
/**
 * how new knowledge bases split their documents, unless they say
 */
export interface Chunking1 {
  overlap?: Overlap;
  size?: Size;
  strategy?: Strategy;
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
 * via the `definition` "McpConnector".
 */
export interface McpConnector {
  auth?: Auth;
  category: Category;
  config: Config5;
  description: Description13;
  docs?: Docs;
  id: Id40;
  local?: Local;
  name: Name36;
  publisher?: Publisher;
  secrets?: Secrets;
  setup?: Setup1;
  source?: Source7;
  verified?: Verified;
}
export interface Config5 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "McpInspectRequest".
 */
export interface McpInspectRequest {
  config: Config6;
  name: Name37;
}
export interface Config6 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "McpToolInfo".
 */
export interface McpToolInfo {
  description?: Description14;
  destructive?: Destructive;
  name: Name38;
  read_only?: ReadOnly;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MeOut".
 */
export interface MeOut {
  email: Email;
  id: Id41;
  memberships: Memberships;
  org_id: OrgId1;
  org_names?: OrgNames;
  portal_url?: PortalUrl1;
  preferences?: Preferences;
  role: Role2;
}
export interface Memberships {
  [k: string]: string;
}
export interface OrgNames {
  [k: string]: string;
}
/**
 * What follows a person from browser to browser. Fields left out of a PUT keep their value.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Preferences".
 */
export interface Preferences {
  simple_mode?: SimpleMode;
  theme?: Theme;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemberIn".
 */
export interface MemberIn {
  email: Email1;
  role: Role3;
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
  role: Role4;
  user_id: UserId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MemoryItem".
 */
export interface MemoryItem {
  created_at?: CreatedAt21;
  key: Key;
  namespace: Namespace;
  updated_at?: UpdatedAt8;
  value: Value3;
}
export interface Value3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "MessageIn".
 */
export interface MessageIn {
  allow_real_runs?: AllowRealRuns;
  mode?: Mode2;
  text: Text4;
}
/**
 * Leave a purpose out to keep its default; null clears it (the project's model, or built-in embeddings).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelDefaultsIn".
 */
export interface ModelDefaultsIn {
  embedding?: Embedding2;
  judge?: Judge;
  simulated_user?: SimulatedUser;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelDefaultsOut".
 */
export interface ModelDefaultsOut {
  defaults: Defaults;
  resolved: Resolved;
}
/**
 * the purposes with a default of their own
 */
export interface Defaults {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Resolved".
 */
export interface Resolved1 {
  model: Model9;
  origin: Origin1;
  problem?: Problem1;
  purpose: Purpose;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelInfo".
 */
export interface ModelInfo {
  context_window?: ContextWindow;
  id: Id42;
  label: Label13;
  price_cache_read_per_mtok?: PriceCacheReadPerMtok;
  price_cache_write_per_mtok?: PriceCacheWritePerMtok;
  price_in_per_mtok?: PriceInPerMtok;
  price_out_per_mtok?: PriceOutPerMtok;
  provider: Provider;
  reasoning?: Reasoning;
  retires?: Retires;
  sampling?: Sampling;
  structured_output?: StructuredOutput1;
  tools?: Tools3;
  vision?: Vision;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ModelOption".
 */
export interface ModelOption {
  id: Id43;
  label: Label14;
  local?: Local1;
  provider: Provider1;
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
  name: Name39;
  workflow_id?: WorkflowId6;
  writes?: Writes;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "NodeManifest".
 */
export interface NodeManifest {
  builtin_tools?: BuiltinTools;
  category: Category1;
  channel_fields?: ChannelFields;
  channel_map_fields?: ChannelMapFields;
  color?: Color;
  conditional_writes?: ConditionalWrites;
  config_schema?: ConfigSchema1;
  description?: Description15;
  docs?: Docs1;
  dynamic_outputs?: DynamicOutputs;
  hosted_forbidden?: HostedForbidden;
  icon?: Icon;
  impl?: Impl;
  inputs?: Inputs;
  interrupts?: Interrupts1;
  kind: Kind8;
  label: Label15;
  list_read_fields?: ListReadFields;
  list_write_fields?: ListWriteFields;
  outputs?: Outputs;
  provenance?: Provenance;
  read_fields?: ReadFields;
  reads?: Reads;
  side_effects?: SideEffects;
  stability?: Stability;
  targets?: Targets4;
  template_fields?: TemplateFields;
  type: Type7;
  version?: Version10;
  writes?: Writes1;
}
export interface ConditionalWrites {
  [k: string]: string[];
}
export interface ConfigSchema1 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PortSpec".
 */
export interface PortSpec {
  description?: Description16;
  multi?: Multi;
  name: Name40;
  ordered?: Ordered;
  required?: Required1;
  type: Type6;
}
export interface Targets4 {
  [k: string]: "full" | "partial" | "none";
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Observation".
 */
export interface Observation {
  error?: Error6;
  expected_trajectory?: ExpectedTrajectory;
  input?: Input7;
  output?: Output4;
  reference?: Reference4;
  status?: Status10;
  trajectory?: Trajectory3;
}
export interface Input7 {
  [k: string]: unknown;
}
export interface Output4 {
  [k: string]: unknown;
}
export interface Reference4 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OnlineIn".
 */
export interface OnlineIn {
  alert_below?: AlertBelow;
  alert_drop?: AlertDrop;
  evaluator_id: EvaluatorId;
  filter?: FilterIn;
  min_samples?: MinSamples;
  sampling?: Sampling1;
  window_minutes?: WindowMinutes;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OnlineOut".
 */
export interface OnlineOut {
  alert_below?: AlertBelow1;
  alert_drop?: AlertDrop1;
  created_at: CreatedAt22;
  deployment_id: DeploymentId1;
  evaluator: Evaluator5;
  evaluator_id: EvaluatorId1;
  filter?: FilterIn;
  id: Id44;
  min_samples?: MinSamples1;
  sampling?: Sampling2;
  window_minutes?: WindowMinutes1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OnlinePatch".
 */
export interface OnlinePatch {
  alert_below?: AlertBelow2;
  alert_drop?: AlertDrop2;
  filter?: FilterIn | null;
  min_samples?: MinSamples2;
  sampling?: Sampling3;
  window_minutes?: WindowMinutes2;
}
/**
 * What an organisation sets for itself; a field left out keeps its value, null returns it to the server's.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgLimits".
 */
export interface OrgLimits {
  allow_local_mcp?: AllowLocalMcp;
  audit_retention_days?: AuditRetentionDays;
  memory_retention_days?: MemoryRetentionDays;
  rate_limit_per_deployment?: RateLimitPerDeployment;
  rate_limit_per_project?: RateLimitPerProject;
  rate_limit_per_share_client?: RateLimitPerShareClient;
  rate_limit_per_webhook?: RateLimitPerWebhook;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgLimitsOut".
 */
export interface OrgLimitsOut {
  effective: Effective;
  own: OrgLimits1;
  server: Server;
}
/**
 * what applies now
 */
export interface Effective {
  [k: string]: number | boolean;
}
/**
 * What an organisation sets for itself; a field left out keeps its value, null returns it to the server's.
 */
export interface OrgLimits1 {
  allow_local_mcp?: AllowLocalMcp;
  audit_retention_days?: AuditRetentionDays;
  memory_retention_days?: MemoryRetentionDays;
  rate_limit_per_deployment?: RateLimitPerDeployment;
  rate_limit_per_project?: RateLimitPerProject;
  rate_limit_per_share_client?: RateLimitPerShareClient;
  rate_limit_per_webhook?: RateLimitPerWebhook;
}
/**
 * the server's values: the default and, mostly, the ceiling
 */
export interface Server {
  [k: string]: number | boolean;
}
/**
 * Fields left out keep their value; `retention_days: null` returns to the server default.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgSettings".
 */
export interface OrgSettings {
  environments?: Environments;
  /**
   * local MCP, rate limits, audit and memory retention: what is sent changes, null clears
   */
  limits?: OrgLimits | null;
  name?: Name41;
  retention_days?: RetentionDays;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "OrgSettingsOut".
 */
export interface OrgSettingsOut {
  audit_retention_days: AuditRetentionDays1;
  effective_retention_days: EffectiveRetentionDays;
  environments: Environments1;
  limits: OrgLimitsOut1;
  memory_retention_days: MemoryRetentionDays1;
  name: Name42;
  name_editable: NameEditable;
  offered_environments: OfferedEnvironments;
  retention_days: RetentionDays1;
}
/**
 * the settings an organisation may override, and what applies
 */
export interface OrgLimitsOut1 {
  effective: Effective;
  own: OrgLimits1;
  server: Server;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PolicyIn".
 */
export interface PolicyIn {
  description?: Description17;
  enabled?: Enabled3;
  environments?: Environments2;
  match?: Match;
  name: Name43;
  require: Require;
  scope?: Scope;
  severity?: Severity2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PolicyOut".
 */
export interface PolicyOut {
  created_at: CreatedAt23;
  description?: Description18;
  enabled?: Enabled4;
  environments?: Environments3;
  id: Id45;
  match?: Match1;
  name: Name44;
  require: Require1;
  scope?: Scope1;
  severity?: Severity3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PortabilityOut".
 */
export interface PortabilityOut {
  adapter: Adapter;
  blockers: Blockers;
  emulated: Emulated;
  label: Label16;
  ok: Ok2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Progress".
 */
export interface Progress {
  done: Done;
  pending: Pending1;
  total: Total;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectIn".
 */
export interface ProjectIn {
  name: Name45;
  workspace_id?: WorkspaceId;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectOut".
 */
export interface ProjectOut {
  created_at: CreatedAt24;
  id: Id46;
  name: Name46;
  workspace_id?: WorkspaceId1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectRenameIn".
 */
export interface ProjectRenameIn {
  name: Name47;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProjectWorkspaceIn".
 */
export interface ProjectWorkspaceIn {
  workspace_id?: WorkspaceId2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProposalDecision".
 */
export interface ProposalDecision {
  commands?: Commands1;
  files?: Files3;
  notes?: Notes2;
  status: Status11;
  tests?: Tests1;
}
/**
 * A model provider: the `provider:` prefix of a model id, its integration and where its API key comes from.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ProviderInfo".
 */
export interface ProviderInfo {
  base_url?: BaseUrl3;
  base_url_required?: BaseUrlRequired;
  example?: Example1;
  id: Id47;
  installed?: Installed1;
  key_env?: KeyEnv;
  label: Label17;
  local?: Local2;
  module: Module;
  notes?: Notes3;
  optional_key_env?: OptionalKeyEnv;
  package: Package;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PublishIn".
 */
export interface PublishIn {
  environment?: Environment2;
  message?: Message10;
  override_reason?: OverrideReason;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PublishOut".
 */
export interface PublishOut {
  api_key?: ApiKey4;
  checklist: Checklist;
  deployment: DeploymentOut;
  /**
   * the pull request opened or updated for this version (G-14)
   */
  git?: GitSyncOut | null;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PullIn".
 */
export interface PullIn {
  overwrite?: Overwrite;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PullOut".
 */
export interface PullOut {
  outcome: Outcome;
  summary: Summary6;
  version: Version11;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "PushOut".
 */
export interface PushOut {
  base: Base;
  branch: Branch4;
  changed: Changed1;
  commit: Commit1;
  created_branch: CreatedBranch1;
  label: Label18;
  note?: Note;
  pr_created?: PrCreated;
  pr_number?: PrNumber2;
  pr_url?: PrUrl2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "QueueIn".
 */
export interface QueueIn {
  instructions?: Instructions;
  name: Name48;
  reviewers_per_item?: ReviewersPerItem;
  rubric: Rubric;
  rules?: Rules;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "Rules".
 */
export interface Rules {
  failed?: Failed1;
  scores_below?: ScoresBelow;
  thumbs_down?: ThumbsDown;
}
/**
 * online evaluator name -> runs scoring below this
 */
export interface ScoresBelow {
  [k: string]: number;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "QueueOut".
 */
export interface QueueOut {
  agreement?: Agreement;
  created_at: CreatedAt25;
  id: Id48;
  instructions?: Instructions1;
  name: Name49;
  progress: Progress;
  reviewers_per_item?: ReviewersPerItem1;
  rubric: Rubric1;
  rules?: Rules;
  workflow_id: WorkflowId7;
}
/**
 * per criterion, 0-1, over items two or more people reviewed
 */
export interface Agreement {
  [k: string]: number | null;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "QueuePatch".
 */
export interface QueuePatch {
  instructions?: Instructions2;
  name?: Name50;
  reviewers_per_item?: ReviewersPerItem2;
  rules?: Rules | null;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RecrawlIn".
 */
export interface RecrawlIn {
  every_hours?: EveryHours1;
}
/**
 * Commands the editor dispatches like its own edits (optimistic, autosaved, undoable).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RefactorOut".
 */
export interface RefactorOut {
  commands: Commands2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ReplaceIR".
 */
export interface ReplaceIR {
  ir: Ir3;
}
export interface Ir3 {
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
  value?: Value4;
}
export interface Value4 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "RunOut".
 */
export interface RunOut {
  cost_usd?: CostUsd3;
  ended_at: EndedAt1;
  error: Error7;
  id: Id49;
  ir_hash: IrHash3;
  mode: Mode3;
  started_at: StartedAt;
  status: Status12;
  thread_id: ThreadId8;
  tokens_in?: TokensIn1;
  tokens_out?: TokensOut1;
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
  input?: Input8;
  mocks?: Mocks2;
  mode?: Mode4;
  pins?: Pins2;
  stream?: Stream2;
  thread_id?: ThreadId9;
}
export interface Context5 {
  [k: string]: unknown;
}
export interface Input8 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SandboxOut".
 */
export interface SandboxOut {
  applies_inject: AppliesInject;
  enforces_egress: EnforcesEgress;
  error?: Error8;
  mode: Mode5;
  /**
   * the saved policy; null when sandbox.json is invalid
   */
  policy: SandboxPolicy | null;
  problems?: Problems3;
  refuses_egress: RefusesEgress;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SandboxPolicy".
 */
export interface SandboxPolicy {
  egress?: Egress;
  inject?: Inject;
  memory_mb?: MemoryMb;
  timeout_s?: TimeoutS;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SavedFile".
 */
export interface SavedFile {
  diagnostics?: Diagnostics4;
  modified: Modified1;
  path: Path6;
  size: Size2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ScoredRunOut".
 */
export interface ScoredRunOut {
  run_id: RunId8;
  scores: Scores4;
  started_at: StartedAt1;
  status: Status13;
  thread_id: ThreadId10;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SearchIn".
 */
export interface SearchIn {
  min_score?: MinScore;
  mode?: Mode6;
  query: Query;
  top_k?: TopK;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SearchOut".
 */
export interface SearchOut {
  results: Results1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SearchResult".
 */
export interface SearchResult {
  chunk_id: ChunkId;
  cite: Cite;
  doc_id: DocId;
  heading: Heading1;
  page: Page3;
  score: Score3;
  source: Source8;
  text: Text5;
  title: Title4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SecretIn".
 */
export interface SecretIn {
  value: Value5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SecretOut".
 */
export interface SecretOut {
  created_at: CreatedAt26;
  name: Name51;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ServerModelsIn".
 */
export interface ServerModelsIn {
  api_key?: ApiKey5;
  base_url?: BaseUrl4;
  provider: Provider2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ServerModelsOut".
 */
export interface ServerModelsOut {
  error?: Error9;
  models?: Models;
  ok: Ok3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ServerSetting".
 */
export interface ServerSetting {
  env: Env;
  group: Group;
  label: Label19;
  value: Value6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SettingsOut".
 */
export interface SettingsOut {
  api_key?: ApiKey6;
  ask_model?: AskModel1;
  base_url?: BaseUrl5;
  enabled?: Enabled5;
  model?: Model10;
  notes?: Notes4;
  privacy?: Privacy1;
  resolved?: ModelInfoOut | null;
  source?: Source9;
  token_cap?: TokenCap2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SetupIn".
 */
export interface SetupIn {
  api_key?: ApiKey7;
  base_url?: BaseUrl6;
  model: Model11;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SetupOut".
 */
export interface SetupOut {
  default_model?: DefaultModel | null;
  done: Done1;
  key_secret?: KeySecret;
  key_set?: KeySet;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareFeedbackIn".
 */
export interface ShareFeedbackIn {
  comment?: Comment5;
  rating: Rating1;
  visitor: Visitor;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareInfo".
 */
export interface ShareInfo {
  greeting: Greeting;
  input: InputShape;
  suggestions: Suggestions;
  title: Title5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareOut".
 */
export interface ShareOut {
  deployment_id: DeploymentId2;
  enabled: Enabled6;
  frame_origins: FrameOrigins;
  greeting: Greeting1;
  input: InputShape;
  input_field: InputField;
  inputs: Inputs1;
  path: Path7;
  suggestions: Suggestions1;
  title: Title6;
  token: Token;
  updated_at?: UpdatedAt9;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareResumeIn".
 */
export interface ShareResumeIn {
  approve?: Approve;
  interrupt_id?: InterruptId2;
  text?: Text6;
  visitor: Visitor1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareRunIn".
 */
export interface ShareRunIn {
  message: Message11;
  thread_id?: ThreadId11;
  visitor: Visitor2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ShareSettingsIn".
 */
export interface ShareSettingsIn {
  enabled?: Enabled7;
  frame_origins?: FrameOrigins1;
  greeting?: Greeting2;
  input_field?: InputField1;
  suggestions?: Suggestions2;
  title?: Title7;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SitemapIn".
 */
export interface SitemapIn {
  url: Url3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendOut".
 */
export interface SpendOut {
  alerts: Alerts;
  day: SpendPeriod;
  month: SpendPeriod;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendPeriod".
 */
export interface SpendPeriod {
  cache_hits: CacheHits;
  fallbacks: Fallbacks;
  limit_usd: LimitUsd1;
  period: Period1;
  period_start: PeriodStart1;
  runs: Runs2;
  spent_usd: SpentUsd1;
  tokens: Tokens1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendPerson".
 */
export interface SpendPerson {
  cache_hits: CacheHits1;
  cost_usd: CostUsd4;
  email: Email3;
  fallbacks: Fallbacks1;
  id: Id50;
  name: Name52;
  runs: Runs3;
  tokens_in: TokensIn2;
  tokens_out: TokensOut2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendProject".
 */
export interface SpendProject {
  cache_hits: CacheHits2;
  cost_usd: CostUsd5;
  fallbacks: Fallbacks2;
  id: Id51;
  name: Name53;
  runs: Runs4;
  tokens_in: TokensIn3;
  tokens_out: TokensOut3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendTotal".
 */
export interface SpendTotal {
  cache_hits: CacheHits3;
  cost_usd: CostUsd6;
  fallbacks: Fallbacks3;
  runs: Runs5;
  tokens_in: TokensIn4;
  tokens_out: TokensOut4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendWorkflow".
 */
export interface SpendWorkflow {
  cache_hits: CacheHits4;
  cost_usd: CostUsd7;
  fallbacks: Fallbacks4;
  id: Id52;
  name: Name54;
  project_id: ProjectId5;
  runs: Runs6;
  tokens_in: TokensIn5;
  tokens_out: TokensOut5;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SpendingOut".
 */
export interface SpendingOut {
  by_person: ByPerson;
  by_project: ByProject;
  by_workflow: ByWorkflow;
  since: Since;
  total: SpendTotal;
  until: Until;
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
 * via the `definition` "StopOut".
 */
export interface StopOut {
  stopped: Stopped;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "SubgraphRequest".
 */
export interface SubgraphRequest {
  name?: Name55;
  node_ids: NodeIds;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TemplateInfo".
 */
export interface TemplateInfo {
  block_count?: BlockCount;
  category?: Category2;
  description: Description19;
  difficulty?: Difficulty;
  id: Id53;
  kit?: Kit;
  name: Name56;
  node_count: NodeCount;
  sample_input?: SampleInput;
  tags?: Tags;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestCaseIn".
 */
export interface TestCaseIn {
  context?: Context6;
  expect?: Expect;
  input?: Input9;
  mocks?: Mocks3;
  name: Name57;
  pins?: Pins3;
}
export interface Input9 {
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
  created_at: CreatedAt27;
  expect?: Expect;
  id: Id54;
  input?: Input10;
  last_result?: CaseResult | null;
  mocks?: Mocks4;
  name: Name58;
  pins?: Pins4;
}
export interface Input10 {
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
  version?: Version12;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TestRunOut".
 */
export interface TestRunOut {
  failed: Failed2;
  passed: Passed3;
  results: Results2;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TextIn".
 */
export interface TextIn {
  text: Text7;
  title: Title8;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "ThreadOut".
 */
export interface ThreadOut {
  cost_usd?: CostUsd8;
  created_at: CreatedAt28;
  deployment_id?: DeploymentId3;
  id: Id55;
  last_ended_at?: LastEndedAt;
  last_error?: LastError1;
  last_run_id?: LastRunId;
  last_started_at?: LastStartedAt;
  runs?: Runs7;
  status: Status14;
  title: Title9;
  tokens?: Tokens2;
  updated_at: UpdatedAt10;
  workflow_id: WorkflowId8;
  workflow_name?: WorkflowName1;
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
 * A project's trace destination (stored in the project's settings under `trace_export`).
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TraceExport".
 */
export interface TraceExport {
  api_key?: ApiKey8;
  enabled?: Enabled8;
  endpoint?: Endpoint2;
  headers?: Headers1;
  include_content?: IncludeContent;
  kind: Kind9;
  public_key?: PublicKey;
  secret_key?: SecretKey;
}
/**
 * otlp: extra headers
 */
export interface Headers1 {
  [k: string]:
    | string
    | {
        [k: string]: string;
      };
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TraceExportOut".
 */
export interface TraceExportOut {
  config: TraceExport | null;
  defaults: Defaults1;
  guides: Guides;
  missing_secrets: MissingSecrets;
}
export interface Defaults1 {
  [k: string]: string;
}
export interface Guides {
  [k: string]: string;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerCreated".
 */
export interface TriggerCreated {
  config: Config7;
  created_at: CreatedAt29;
  deployment_id: DeploymentId4;
  enabled: Enabled9;
  id: Id56;
  kind: Kind10;
  secret?: Secret;
  url?: Url4;
}
export interface Config7 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerIn".
 */
export interface TriggerIn {
  config?: Config8;
  deployment_id?: DeploymentId5;
  kind: Kind11;
}
export interface Config8 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "TriggerOut".
 */
export interface TriggerOut {
  config: Config9;
  created_at: CreatedAt30;
  deployment_id: DeploymentId6;
  enabled: Enabled10;
  id: Id57;
  kind: Kind12;
  url?: Url5;
}
export interface Config9 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "UrlIn".
 */
export interface UrlIn {
  url: Url6;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionDetail".
 */
export interface VersionDetail {
  copilot_changes?: CopilotChanges;
  created_at: CreatedAt31;
  created_by: CreatedBy2;
  id: Id58;
  ir: Ir4;
  ir_hash: IrHash4;
  layout: Layout3;
  message: Message12;
  semver: Semver1;
  workspace_commit: WorkspaceCommit;
}
export interface Ir4 {
  [k: string]: unknown;
}
export interface Layout3 {
  [k: string]: unknown;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionIn".
 */
export interface VersionIn {
  bump?: Bump;
  message?: Message13;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "VersionOut".
 */
export interface VersionOut {
  copilot_changes?: CopilotChanges1;
  created_at: CreatedAt32;
  created_by: CreatedBy3;
  id: Id59;
  ir_hash: IrHash5;
  message: Message14;
  semver: Semver2;
  workspace_commit: WorkspaceCommit1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowIn".
 */
export interface WorkflowIn {
  model?: Model12;
  name?: Name59;
  system_prompt?: SystemPrompt1;
  temperature?: Temperature1;
  tools?: Tools4;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowOut".
 */
export interface WorkflowOut {
  created_at: CreatedAt33;
  diagnostics: Diagnostics5;
  id: Id60;
  ir: Ir5;
  ir_hash: IrHash6;
  /**
   * created from a template: the files, dataset, evaluators, tests and schedule it added
   */
  kit?: KitOut | null;
  layout: Layout4;
  name: Name60;
  project_id: ProjectId6;
  updated_at: UpdatedAt11;
  version: Version13;
}
export interface Ir5 {
  [k: string]: unknown;
}
export interface Layout4 {
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
  id: Id61;
  name: Name61;
  updated_at: UpdatedAt12;
  version: Version14;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkflowPatch".
 */
export interface WorkflowPatch {
  name: Name62;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "WorkspaceOut".
 */
export interface WorkspaceOut {
  id: Id62;
  is_default: IsDefault;
  name: Name63;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__datasets__PushIn".
 */
export interface PushIn {
  version?: Version15;
}
/**
 * A file reference, as a run's input carries it.
 *
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__files__FileOut".
 */
export interface FileOut1 {
  id: Id63;
  kind?: Kind13;
  media_type: MediaType;
  name: Name64;
  size: Size3;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__gitsync__PushIn".
 */
export interface PushIn1 {
  message?: Message15;
  overwrite?: Overwrite1;
  version?: Version16;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__setup__TestOut".
 */
export interface TestOut {
  error?: Error10;
  ok: Ok4;
  reply?: Reply1;
}
/**
 * This interface was referenced by `ApiSchemas`'s JSON-Schema
 * via the `definition` "agentcanvas__api__v1__traces__TestOut".
 */
export interface TestOut1 {
  detail: Detail4;
  ok: Ok5;
}
