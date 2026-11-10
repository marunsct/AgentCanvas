# Upgrading an agent framework

AgentCanvas runs and exports workflows on eleven agent frameworks. Each one releases often (some weekly) and several
ship breaking changes in minor versions. This page is the procedure for moving one of them to a new release, and how
we find out that one exists.

## How we find out

- **Dependabot** watches `infra/frameworks/requirements.txt`, a generated list of every Python framework pin, and
  `apps/api/agentcanvas/adapters/mastra/package.json`. It opens one pull request per framework each week, for a
  release at least 7 days old (14 for a major version). LiteLLM (held, see [decisions.md](../decisions.md)) and
  major versions of Agno's OpenAI client are ignored there on purpose.
- **`make framework-versions`** prints every pinned package with its latest stable release, release date and the
  official changelog to read. Run it before a release, or whenever a framework announces something.
- The pull request Dependabot opens **fails `tests/test_framework_versions.py` by design**: it changed only the
  mirror file. Taking the update means doing the steps below on a branch, not merging the bot's change as it is.

## The steps, for one framework at a time

1. **Read the official changelog** for every release between our pin and the target (the link is in
   `make framework-versions`). Use only the project's own releases page, changelog, migration guide or docs. For each
   change, check whether our code uses that API: the adapter in `apps/api/agentcanvas/adapters/<framework>/`
   (`codegen.py` writes exported projects; the runtime runs flows), `adapters/langchain/interop.py` (the "Agent from
   another framework" block), `scripts/smoke_frameworks.py`, and the conformance kit. Write down what changes for us.
2. **Move the pin everywhere**: `make framework-bump PKG=<package> VERSION=<version>`. It rewrites exports, CI, the
   image, docs, code-generator constants and manifests, regenerates the mirror file, re-locks the framework
   environments (below), and runs `tests/test_framework_pins.py`, which fails if any place was missed. Then
   `cd apps/api && uv run python scripts/export_ir_schema.py` (the guide copy the Copilot reads names the version).
3. **Rebuild the framework's environment**: `make framework FW=<id>` (`make crewai`, `make mastra` for those two).
4. **Make the code changes** from step 1, each with a test that fails without it.
5. **Prove it**, in this order:
   - `uv run pytest tests/test_conformance.py -k <id>`: the framework runs our conformance workflows;
   - `uv run --with "<pins>" python scripts/smoke_frameworks.py <id>`: a real agent of that framework is wrapped, drawn
     and run, and its exports' own tests pass (the pins are in `scripts/ci-local.sh`);
   - the framework's browser spec (`e2e/s25`–`s36`, see `scripts/ci-local.sh frameworks` for the list);
   - `scripts/ci-local.sh frameworks export` before merging: every framework, every template export's own tests.
6. **Record it**: a changelog line naming the version and anything users notice, and a [decisions.md](../decisions.md) entry for any
   judgement call (a feature not adopted, a pin held back and why).

One framework per pull request: when something breaks, the cause is obvious and the rest can still go out.

## The framework environments' locks

The image (`FRAMEWORKS=...`), `make crewai` and `make framework` install each framework environment from
`apps/api/framework-locks/<id>.txt`: every package, transitive ones included, pinned with its hashes, so two builds
ship the same packages and a hijacked release cannot slip in unreviewed. `tests/test_framework_versions.py` fails
when a lock no longer holds the pins.

- `uv run python scripts/framework_versions.py lock [<id>...]` regenerates them (`make framework-bump` does it). It
  leaves out releases younger than 7 days, except the pinned packages themselves and the sibling packages a new pin
  requires at its own version: a malicious release is usually found and pulled within days.
- To take a security fix published less than 7 days ago, pass `--cooldown 0`.
- When a package caps a dependency below its fixed version and the cap does not apply to how we run it, write the
  requirement in `framework-locks/<id>.overrides.txt` with the reason (`google_adk.overrides.txt` raises filelock past
  LiteLLM's cap). Remove it once the pin that set the cap moves on.
- `scripts/audit-deps.sh` (`scripts/ci-local.sh audit`) checks every lock, with the API's and Mastra's, against the
  advisory databases; an advisory accepted there is listed in the script with its reason.

## Holding a version back

When a release breaks something we cannot fix on our side, keep the old pin, add an `ignore` for that range to
`.github/dependabot.yml` with the reason, and add a dated line to [hardening.md](../hardening.md) "Watch" saying what to recheck. LiteLLM
is the current example.

## What makes upgrades cheap here

- One source per pin (`designs.AGENT_FRAMEWORKS`, the bridges, Mastra's package.json); every other place is checked
  against it by `tests/test_framework_pins.py` and rewritten by `framework_versions.py bump`.
- The conformance kit and the smoke checks run real framework code against fake models, so an upgrade is tested
  without model keys or cost.
- Upstream bugs we work around have a strict canary in `tests/test_upstream_canaries.py`: the day upstream fixes one,
  the canary turns red and the workaround can go (LangGraph 1.2.20 retired one this way).
