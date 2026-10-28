# Settings and moving around

## The top bar

Every page has the same bar: the project you are working in (click it to switch to another project or, if you
build, make a new one), the pages (**Workflows**, **Playground**, **Inbox** with the number of decisions waiting,
**Knowledge**, **Memory**, **Workspace**, **Events**), **Settings**, and your initial. Your initial opens a menu with
your email and role, the organisation (and, with Matura, a switch to your other organisations), the colour theme,
**User guide** (this guide, inside the app) and **Sign out**. The workflow editor keeps its own bar of canvas tools and has the same menu at its right.

Links your role cannot use are not shown: viewers see no Playground, for example.

## Settings

**Settings** groups everything that is configured rather than built, by who it applies to. Sections your role
cannot change are shown read-only, with a sentence saying who can; sections your role cannot read are not listed.

| Section | What it holds | Who changes it |
|---|---|---|
| Project › **General** | The project's name and id; deleting the project with everything in it (you type its name to confirm; an organisation keeps at least one project) | admins |
| Project › **Models** | The project's model and its key (the first-run screen), and the [default model for each purpose](models.md#which-model-is-used-when-nothing-names-one): judges, simulated users, knowledge embeddings | builders |
| Project › **Copilot** | Whether the Copilot is on, its model (the project's, or a separate one with its own address and key), a model for questions, privacy, the project notes it always reads, its token limit | builders |
| Project › **Secrets** | API keys and passwords workflows use; values are write-only. Deleting one asks first | builders |
| Project › **Sandbox** | Time and memory limits for workspace code, and the hosts it may reach, where the server can enforce that ([Workspace › Sandbox](workspace.md#sandbox)) | builders |
| Project › **Observability** | Sending traces to Langfuse, Phoenix or any OpenTelemetry collector | admins |
| Organisation › **General** | The organisation's name and how long conversations are kept; the server's own retentions, for reference | admins |
| Organisation › **Members**, **Policies**, **Audit log** | See [Organisations, roles and policies](accounts.md) | admins |
| Organisation › **Server** | How this server is set up (sign-in, where workspace code runs, frameworks offered, limits, retention), each with the environment variable an operator sets it with. Read-only | admins can read |
| You › **Appearance** | The colour theme of this browser | you |

The addresses pages had before Settings existed (`/setup`, `/members`, `/audit`, `/policies`) still work and open
the matching section.
