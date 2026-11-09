# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue, pull request or discussion.

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a
vulnerability**, or go straight to <https://github.com/marunsct/AgentCanvas/security/advisories/new>. Only the
maintainers see the report.

If that form is not available, open an issue that asks for a private contact, with no details of the problem.

A useful report says:

- which release or commit you tested, and how it was deployed (Docker app, production compose file, Helm chart,
  from source);
- what an attacker can do, with the steps or a proof of concept to reproduce it;
- any settings it depends on (sign-in mode, where workspace code runs, the database).

Please test only against an installation you run yourself, never against someone else's server or data.

## Supported versions

AgentCanvas is released as one line of versions (0.x, see [CHANGELOG.md](../CHANGELOG.md)). Security fixes go into
the next release from `main`; older releases are not patched. Run the latest release.

| Version | Security fixes |
|---|---|
| the latest release and `main` | yes |
| any earlier release | no: upgrade |

## Response targets

These are targets, not contractual commitments:

| Step | Target |
|---|---|
| Acknowledge the report | within 3 business days |
| First assessment (do we reproduce it, how severe is it) | within 10 business days |
| Fix released, critical or high severity | within 30 days of the assessment |
| Fix released, medium or low severity | within 90 days of the assessment |

We keep you informed while we work on it, agree a disclosure date with you, publish a GitHub security advisory with
the fix, and credit you there unless you prefer not to be named.

## What is in scope

The code in this repository: the API (`apps/api`), the web app (`apps/web`), the images, compose files and Helm
chart (`infra/`), and the code AgentCanvas generates into exported projects. Vulnerabilities in a dependency belong
to that project, but tell us when AgentCanvas is exposed through it.

How AgentCanvas handles your data, and what it relies on you to provide, is described in the user guide's
[Security, privacy and compliance](../docs/user-guide/security.md) page.
