# Developer documentation

For people who change AgentCanvas: the code, its design and the project's working files. To use AgentCanvas, or to
run it for a team, read the [user guide](../user-guide/index.md) instead.

## Start here

1. The [repository README](https://github.com/marunsct/AgentCanvas/blob/main/README.md): what AgentCanvas is and the quick start.
2. [handover.md](handover.md): the owner's standing decisions, the repository map, setup and commands, CI, the
   conventions and the quirks of the cloud sessions.
3. [hardening.md](hardening.md): what is open and planned. This is the one status page; nothing else keeps a status.
4. The [build guide](build-guide/README.md) for how the code is built, and the [spec](spec/README.md) and
   [ADRs](adr/README.md) for why it is built that way.

## Where things are

| Document | What it holds | Kept current |
|---|---|---|
| [hardening.md](hardening.md) | Open items, the improvement plan (F-1 to F-14), what we decided not to build, dated things to watch, the owner's to-dos | In every PR: delete what you closed, add what you found |
| [decisions.md](decisions.md) | Judgment calls made without asking the owner, newest first, each with how to reverse it | When you make one |
| [CHANGELOG.md](https://github.com/marunsct/AgentCanvas/blob/main/CHANGELOG.md) | What changed, release by release; "Unreleased" at the top | In every PR |
| [handover.md](handover.md) | How to work on the project (see above) | When a convention, a command or an owner decision changes |
| [spec/](spec/README.md) | The functional and technical specification (v0.6), the [node catalog](spec/04-node-catalog.md), the [identity and tenancy plan](spec/18-identity-and-tenancy-plan.md), the [threat model](spec/19-threat-model.md), and [notes per framework adapter](spec/adapters/README.md) | The threat model when a boundary moves; the rest is the design as specified |
| [build-guide/](build-guide/README.md) | Concepts primer, environment, conventions, backend, frontend, compiler and node types, testing, security checklist, troubleshooting, the Copilot's real-model evaluation, framework upgrades, spikes, and the tested reference implementation the code grew from | Reference; fix what you find out of date |
| [adr/](adr/README.md) | Architecture decision records 0001–0019 | Add one for each architecture decision |
| [history/](history/README.md) | Plans, logs and reviews the project has moved past | Frozen |

## Common tasks

| To… | Read |
|---|---|
| Set up, run the API and the web app, run the checks | [handover.md §3](handover.md#3-setup-and-commands) and [§4 CI](handover.md#4-ci); `make check`, `make ci` |
| Add a node type | [build-guide/07-compiler-and-node-types.md](build-guide/07-compiler-and-node-types.md) |
| Write tests the right way (fake models, Postgres, e2e) | [build-guide/08-testing-and-quality.md](build-guide/08-testing-and-quality.md) |
| Move a framework to a new version | [build-guide/16-framework-upgrades.md](build-guide/16-framework-upgrades.md) |
| Measure the Copilot with a real model | [build-guide/14-copilot-evaluation.md](build-guide/14-copilot-evaluation.md) |
| Change a security boundary (sandbox, network, secrets, tenants) | [spec/19-threat-model.md](spec/19-threat-model.md) and a [decisions.md](decisions.md) entry |
| Fix a known build or library problem | [build-guide/11-troubleshooting-faq.md](build-guide/11-troubleshooting-faq.md) |

## Keeping the docs current

- **One home for each kind of information.** Status and plans: [hardening.md](hardening.md). What changed:
  [CHANGELOG.md](https://github.com/marunsct/AgentCanvas/blob/main/CHANGELOG.md). Judgment calls: [decisions.md](decisions.md). How to use or operate
  AgentCanvas: the [user guide](../user-guide/index.md). How the code works: the build guide, the spec and the ADRs.
  Do not write a status or a to-do list anywhere else; link to the home instead.
- **The user guide is for people who use or operate AgentCanvas.** Repository internals, test suites and how the
  code is built go here, not there. Every user-visible change updates its page in the same PR.
- **The user guide has copies.** `make schema` (`apps/api/scripts/export_ir_schema.py`) copies every page into
  `apps/api/agentcanvas/copilot/knowledge/user-guide/`, which the Copilot searches; commit the copies with the page.
  [Server settings](../user-guide/server-settings.md) is generated from `apps/api/agentcanvas/settings.py` by
  `apps/api/scripts/export_settings_reference.py`; `tests/test_settings_reference.py` fails when it is stale.
- **Tests read some pages by path:** `test_deploy_files.py` (Deploy and operate, and the Operations reference, whose
  runbook anchors `infra/observability/alerts.yaml` links to), `test_copilot_evals.py` (the Copilot page and
  build-guide/14), `test_framework_pins.py` (Connect to other agents and tools) and `test_settings_reference.py`.
  Moving or renaming one of them means changing the test in the same PR.
- **The site.** A new page goes into `mkdocs.yml` (User guide or Developer). `make docs` serves the site; `make ci`
  builds it with `--strict`. Use relative links between documents.
- **When a plan or log stops being kept current,** move it to [history/](history/README.md) with a one-line
  "Historical" note at the top that says where the current information is, and list it in the history README.
