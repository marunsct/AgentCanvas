# Security, privacy and compliance

This page is for whoever answers a security review or a customer's questionnaire about AgentCanvas. It says where
data is kept, how it is protected, where it goes and what AgentCanvas leaves to you. Each section links to the page
with the detail.

AgentCanvas is software you run yourself, on your own servers or in your own cloud account. The maintainers do not
host it, have no access to your installation and receive nothing from it: AgentCanvas sends no usage data, and it
switches off the usage telemetry of the agent frameworks that have one (CrewAI, Google ADK, Agno, Mastra, Strands,
and the OpenAI Agents SDK's tracing to OpenAI). Whoever runs the server controls every setting on this page.

## Where your data is stored

Everything AgentCanvas keeps is in two places you provision: the **Postgres database** and the **workspaces
volume** ([What runs](deploy.md#what-runs)). The run store (checkpoints and long-term memory) is in the same
database unless the operator points it at another one (`AGENTCANVAS_PERSISTENCE_URL`).

| Data | Where |
|---|---|
| Organisations, people, memberships and roles | the database |
| Projects, workflows and their versions, deployments, triggers, policies | the database |
| Conversations: runs, their inputs, outputs and step-by-step events, pending approvals, saved state (checkpoints) | the database |
| Files people attach to runs | the database |
| Long-term memory, knowledge bases (documents, passages and their embeddings) | the database |
| Datasets, evaluators, experiments, annotations, Copilot chats, the event log | the database |
| Project secrets, webhook signing secrets, app-connector sign-ins | the database, encrypted (below) |
| The audit log | the database |
| Each project's workspace (its code and `sandbox.json`) | a git repository per project on the workspaces volume |

Logs and metrics go wherever your platform collects the containers' output and scrapes `/metrics`. A local
installation with SQLite keeps the database in one file on that computer.

## Encryption

**In transit.** The web container speaks plain HTTP: terminate TLS at your load balancer, ingress or proxy
([Option 1](deploy.md#option-1-docker-compose-on-one-host)). Behind HTTPS it sends HSTS and a strict
Content-Security-Policy, and the web app loads nothing from other sites. Connections to the database and to model
providers use TLS as you configure them (`sslmode` in `DATABASE_URL`; providers' HTTPS endpoints).

**At rest, by AgentCanvas.**

- **Secrets** (project secrets, webhook signing secrets, app-connector sign-ins) are encrypted with a data key of
  their organisation (Fernet: AES with an HMAC). Each data key is stored only wrapped by a master key kept outside
  the database: a key file, AWS KMS or Google Cloud KMS (`AGENTCANVAS_KMS_KEY`; without one it is derived from
  `AGENTCANVAS_SECRET_KEY`), and it can be rotated without
  downtime ([Rotating the secrets master key](deploy.md#rotating-the-secrets-master-key)). Secret values cannot be
  read back through the app or the API, and they are masked as `***` in run events, traces and error messages
  ([Secrets](workspace.md#secrets)).
- **Conversation checkpoints** (the saved state a paused run continues from) are encrypted with AES when
  `LANGGRAPH_AES_KEY` is set. The production compose file refuses to start without it, and a staging or production
  server without it says so in its log.
- **Keys and tokens people present** are stored only as hashes: deployment API keys, personal API tokens and the
  SCIM token. A share link's token is kept as it is, so a builder can copy the link again.

**At rest, by you.** Everything else in the database (workflows, run inputs and outputs, memory, knowledge, files)
and the workspaces volume are not encrypted by AgentCanvas. Use your database's and your disks' encryption (every
managed Postgres offers it), and protect the backups the same way.

## Organisations and isolation

Everything belongs to an organisation, and nobody sees another organisation's data
([Organisations](accounts.md#organisations)). The API scopes every request to the organisation of the person or
key making it.

On Postgres, every table that holds an organisation's data also has a row-level security policy, forced on the
tables' owner too. When people sign in through Matura, each request's database session is narrowed to its
organisation, so a query that forgot its filter still returns nothing from another organisation. With OpenID
Connect sign-in the API's own filters are the isolation. The policies hold only for a database role that cannot
skip them: the production compose file connects the API as such a role, and so does the Helm chart when it is given
the owner's credentials for migrations ([Database isolation](deploy.md#database-isolation)). SQLite, for local use,
has no row-level security.

## Sign-in, roles and access

- **Single sign-on** through any OpenID Connect provider (Keycloak, Okta, Entra ID, Auth0, Google...), or through
  Matura. A production server refuses to run without sign-in ([Sign-in](deploy.md#sign-in)).
- **Roles**: viewer, reviewer, builder and admin, per organisation, given by an admin or by your directory's groups
  ([Roles](accounts.md#roles), [Roles from groups](accounts.md#roles-from-groups)).
- **SCIM 2.0** provisioning keeps members in step with your directory: deactivating someone there suspends their
  access and their API tokens at once ([SCIM](accounts.md#provisioning-from-your-directory-scim)).
- **Policies** an admin writes (for example approved models only, or a person's approval before every external
  write) are enforced by the editor and at publish ([Policies](accounts.md#policies)).
- **Programmatic access**: personal API tokens act as their owner with the lower of the two roles; each published
  deployment has its own API key, which can be rotated; public calls are rate-limited per deployment, webhook,
  project and share-link visitor ([API tokens](accounts.md#api-tokens)).
- **Allowed networks**: an admin can accept the organisation's requests only from listed networks, for every kind of
  credential, sign-ins, API tokens, deployment keys, share links and SCIM alike
  ([Allowed networks](accounts.md#allowed-networks)).
- **Multi-factor sign-in**: the operator can require an authentication level or method (`acr`, `amr`) for every
  sign-in, and an admin can require a second factor of the organisation's members; a sign-in without it is sent back
  to the identity provider ([Multi-factor sign-in](accounts.md#multi-factor-sign-in)).

## Workspace code and the sandbox

Builders can add Python code to a project (scripts, tools, custom blocks, event handlers). In the production
compose file and the Helm chart it runs in a separate **sandbox service** that holds no database credentials, no
keys and no other project's files, with time and memory limits. Its only way to the network is an egress proxy
that allows just the hosts the project lists. It never reaches cloud metadata addresses, and reaches private ones
only when the operator allows it. A staging or production server refuses to run workspace code as the API's own
user unless the operator explicitly allows it ([Sandbox](workspace.md#sandbox)).

## Audit log

Every successful change is recorded: who made it (a person, a token, a deployment key or SCIM), what it touched,
when and from which address. Approvals record a hash of exactly what was approved. Reads and failed attempts are
not recorded. Admins read it under **Settings › Audit log** ([Audit log](accounts.md#audit-log)); rows are kept 400
days by default (`AGENTCANVAS_AUDIT_RETENTION_DAYS`). Security-relevant entries (publishing, keys, share links,
secrets, approvals, members, settings and policies, the kill switch, deletions), or every entry, can also be
forwarded to your SIEM as signed CloudEvents or to Splunk's HTTP Event Collector, reliably: they are queued in the
database with the entry and retried until delivered
([Security events and audit forwarding](deploy.md#security-events-and-audit-forwarding)).

The audit log is tamper-evident. Each organisation's entries form a hash chain: every entry stores the SHA-256 hash
of its own contents together with the previous entry's hash, so an entry that is edited, deleted or slipped in
afterwards breaks the chain. Admins check it with `GET /v1/org/audit/verify`, which recomputes every hash and lists
the first entries that do not match (`changed`, `missing` or `unchained`); operators check every organisation with
`python -m agentcanvas.audit verify`, which exits with 1 when a chain is broken (run it from a scheduled job to be
alerted). Retention deletes the oldest entries without breaking the chain: it records where the chain now starts.
Entries written before the chain existed have no hash and are counted as `unchained` at the start. On Postgres,
when the API connects as its own role (`agentcanvas_app`, [Database isolation](deploy.md#database-isolation)),
the database itself refuses to let the API change or delete audit entries; retention deletes old ones through a
function that can do only that. Forwarded entries
carry their `row_hash` and `prev_hash`, so the copy in your SIEM also shows if someone with full database access
rewrote the whole chain; compare the `head_hash` that verifying reports with the last forwarded entry's `row_hash`.

## Third parties your data reaches

AgentCanvas sends data only to the services you configure. There is no subprocessor of the maintainers; the list
below is the services **you** choose, each of which processes data under your own agreement with it.

| Service | What it receives | When |
|---|---|---|
| **Model providers** (Anthropic, OpenAI, Google, Mistral, Groq, DeepSeek, xAI, Azure OpenAI, any OpenAI-compatible API) | prompts, the run's messages and tool results, attached files the model is shown | whenever a workflow, the Copilot, an LLM judge or a simulated user calls a hosted model. Models on your own machines (Ollama, LM Studio, vLLM, llama.cpp) send nothing out; the Copilot's **Private** setting allows only those ([Models](models.md)) |
| **Embedding models** | memory items, knowledge passages and search queries | only when a knowledge base or long-term memory uses a provider's embedding model; the built-in embedder works on the server ([Knowledge](knowledge.md#chunking-and-embeddings)) |
| **LangSmith** | runs as traces, with their inputs and outputs (secrets masked) | only when the operator sets `LANGSMITH_TRACING` and a key |
| **Trace export**: Langfuse, Arize Phoenix, Datadog or any OTLP endpoint | a project's runs as traces, with inputs and outputs (secrets masked), or timings, models and tokens only | only when an admin sets one up for a project (**Settings › Project › Observability**) |
| **Your OpenTelemetry collector** | the server's own request, database and outgoing-call spans | only when the operator sets `OTEL_EXPORTER_OTLP_ENDPOINT` |
| **Tools, app connectors, MCP servers, other agents (A2A), web pages** | what the workflow sends them | only those a builder adds to a workflow or a knowledge base |
| **GitHub** | a workflow's files | only when a builder connects a workflow to a repository |
| **LangSmith Sandbox** | the project's workspace files | only when the operator sets `AGENTCANVAS_SCRIPT_EXECUTION=langsmith` |
| **Your identity provider, Matura** | sign-in; Matura also receives forwarded audit entries when that is set up | as configured |

## Retention and deletion

| What | Kept |
|---|---|
| Conversations (runs, their events, checkpoints and pending decisions) | deleted after 90 days without activity (`AGENTCANVAS_RETENTION_DAYS`; each organisation may set its own, or keep them forever). Running and waiting conversations are kept |
| Files attached to runs | deleted once no conversation has used them for as long as the organisation keeps conversations |
| Audit log | 400 days (`AGENTCANVAS_AUDIT_RETENTION_DAYS`) |
| Long-term memory | forever, unless `AGENTCANVAS_MEMORY_RETENTION_DAYS` is set ([Memory retention](memory.md#retention)) |
| Workflows and versions, deployments, datasets, experiments, evaluators, knowledge bases, Copilot chats, the event log, workspaces | until someone deletes them, or deletes the project |

Details are in [Retention, restore and disaster recovery](operations.md#retention-restore-and-disaster-recovery).

**Erasing one person's data** is one action for a member ([Your data](#your-data)). What they added to datasets,
knowledge bases and workspaces is the project's and stays; remove it by hand if it names them.

## Your data

What AgentCanvas offers to answer data-protection requests (GDPR Art. 15, 17, 20 and 28(3)(g)) and to return or
delete an organisation's data when it leaves. Every action is for admins and is recorded in the audit log with
counts, never the content; exports are recorded too, though reads otherwise are not.

| Request | How |
|---|---|
| A person's own data (Art. 15 and 20) | `GET /v1/me/export`, for anyone signed in: their account and memberships, and in the organisation they act in the projects they were added to, their API tokens (never the secret), app-connector sign-ins, Copilot chats, the conversations they started with each run's input, answer and steps, their approval decisions, long-term memories, files and audit entries, as one JSON file |
| A member's data, for an admin answering a request | `GET /v1/org/members/{user_id}/export`: the same, limited to this organisation (other memberships are not shown) |
| A deployment end user's data | `POST /v1/org/end-users/export` (admins; body `user_id`, optional `deployment_id`, `reason`) or `POST /v1/deployments/{id}/end-users/export` with the deployment's key: their conversations, long-term memories and the names and sizes of the files their runs used |
| Erase a member (Art. 17) | `POST /v1/org/members/{user_id}/erase`: removes them with their tokens, sign-ins, memories and Copilot chats (and, if asked, the conversations they started), and pseudonymises their account when they belong to no other organisation ([Members](accounts.md#members)) |
| Erase a deployment's end user (Art. 17) | by the `user_id` the deployment API was sent: `POST /v1/org/end-users/erase` (admins; one deployment or every one) or `POST /v1/deployments/{id}/end-users/erase` with the deployment's key, so your own systems can answer the request. Their conversations, long-term memories and the files their runs used are deleted. The audit log names them by a hash of the id ([Call a deployment](publish.md#call-a-deployment)) |
| Return the organisation's data (Art. 20, end of contract) | `POST /v1/org/export`: a zip of NDJSON files with every kind of data, the attached files and the workspaces, without credentials, built in the background, downloaded once through a link that works for 15 minutes and deleted after 24 hours (`AGENTCANVAS_ORG_EXPORT_KEEP_HOURS`) ([Exporting the organisation](settings.md#exporting-the-organisation)) |
| Delete the whole organisation (end of contract) | `DELETE /v1/org`: suspended at once, deleted for good after a grace period of 7 days (`AGENTCANVAS_ORG_DELETION_GRACE_DAYS`), which an admin can take back until then ([Deleting the organisation](settings.md#deleting-the-organisation)) |

## Backups and recovery

`infra/scripts/backup.sh` dumps the database and archives the workspaces; `infra/scripts/restore.sh` restores both.
On a managed Postgres, use its point-in-time recovery as well. Keep the master key and `LANGGRAPH_AES_KEY` with the
backups but stored apart from them: without them the secrets and checkpoints in a backup cannot be read
([Backups](deploy.md#backups)).

## Compliance

AgentCanvas holds no certification or attestation of its own (SOC 2, ISO 27001 or similar), and no data
processing agreement with the maintainers applies, since no data reaches them. Your installation runs under your
organisation's controls: the database, network, backups, access to the servers and the agreements with the model
providers you choose are yours. This page, the [Operations reference](operations.md) and the audit log are the
evidence AgentCanvas provides for your own assessments (GDPR, SOC 2, ISO 27001).

The terms under which AgentCanvas and the code it exports may be used have not been published yet: the repository
has no licence file. Ask the maintainers before you rely on any rights to it.

## EU AI Act

When you publish an agent to people in the EU, Article 50 of the AI Act applies to you as its provider or deployer:
people must be told they are interacting with an AI system unless it is obvious (since 2 August 2026), and content
an AI system generates must be marked as such in a machine-readable way (systems already on the market have until
2 December 2026). What AgentCanvas does by default:

- **Disclosure.** The share-link chat page and the chat widget always show an AI notice under the message box,
  which builders may reword per link but not remove (a deployment whose
  [AI-system record](publish.md#ai-system-record) says it serves only your own staff gets wording for colleagues);
  the widget's button is named *AI assistant* unless your site names it otherwise
  ([Share as an app or a chat widget](publish.md#share-as-an-app-or-a-chat-widget)).
- **Marking.** Every answer a deployment gives through its API, MCP, A2A or share link has the header
  `X-AI-Generated: true`, and API results carry `generated_by` with the models that answered
  ([Call a deployment](publish.md#call-a-deployment)).

What stays yours: deciding whether your use is high-risk (Annex III) or needs more than Article 50, labelling deep
fakes and AI-written text published on matters of public interest, keeping the mark when you pass content on, and
telling people about AI in channels AgentCanvas does not render (your own app built on the API, emails or messages
your workflow sends).

## AI-system inventory

ISO/IEC 42001 (Annex A: impact assessment, lifecycle records, event logs), NIST AI 600-1 (governance, pre-deployment
testing, third-party models) and the EU AI Act's deployer duties all ask you to keep an inventory of your AI systems:
what each is for, who answers for it, how risky it is, which models and providers it uses and what testing backs it.
In AgentCanvas each deployment has an [AI-system record](publish.md#ai-system-record) that does most of that work:

| An auditor asks | The record answers from |
|---|---|
| What is it for, who owns it, how risky is it? | The purpose, owner, risk class and rationale people write |
| What data does it handle, and for whom? | The data categories and whether it faces the public |
| Which models and providers (subprocessors)? | The published version's model blocks, backups, app connectors, web APIs, remote agents, judge models and trace export |
| What changed, and who released it? | The version, its changes since the one before, and the audit log's publish entries |
| What stops it misbehaving? | Safety steps, agent add-ons, the budget, the organisation's policies, approval steps and the steps nobody approves |
| What evidence was it tested on? | The release gate, the latest experiment and red-team run, online evaluators' scores |
| How long is its data kept? | Conversation, audit and memory retention, approval expiry, trace redaction |

A record marked high risk without evaluation evidence or an approval step gets a note, not an error: whether a use is
high-risk under Annex III, and what it then needs, stays your decision. The record is evidence for your own
assessment, not a conformity assessment.

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting, as the
[security policy](https://github.com/marunsct/AgentCanvas/security/policy) describes: never in a public issue. The
policy also lists the supported versions and how quickly reports are answered.

The web server publishes the same contact at `/.well-known/security.txt` (RFC 9116), so scanners and researchers
find it. To have reports about your installation reach your own security team instead, set `SECURITY_CONTACT` and
`SECURITY_POLICY` on the web container ([Deploy and operate](deploy.md#essential-settings)).
