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
days by default (`AGENTCANVAS_AUDIT_RETENTION_DAYS`). Security-relevant entries (publishing, keys, secrets,
approvals, members, settings and policies) can also be forwarded to Matura as signed events
([Audit forwarding](deploy.md#audit-forwarding)).

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

**Erasing one person's data** has no single action yet. Today an admin can: remove the person from the organisation
(Settings › Members, or through SCIM); delete their conversations on the **Runs** page; erase their memory
namespace on the **Memory** page ([The Memory page](memory.md#the-memory-page)); and remove what they added to
datasets, knowledge bases and workspaces. Their account record (email and name) stays, and so do their audit-log
entries until the audit log's retention removes them; removing either sooner needs direct access to the database.

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

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting, as the
[security policy](https://github.com/marunsct/AgentCanvas/security/policy) describes: never in a public issue. The
policy also lists the supported versions and how quickly reports are answered.
