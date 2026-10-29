# Organisations, roles and policies

## Signing in

In production AgentCanvas signs people in through your identity provider (any OpenID Connect provider: Keycloak,
Okta, Entra ID, Auth0, Google…). The administrator configures it once (see [Deploy and operate](deploy.md#sign-in));
people then click through to their usual login page. Sessions renew silently. The top bar shows who you are and
your role, with **Sign out** (sign in again to use a different account).

In local mode (`make dev-fake` / `make dev-api` without OIDC) there is no sign-in: you are the built-in admin
`dev@localhost`.

## Organisations

Everything (projects, workflows, runs, secrets, memory, deployments, policies, audit log) belongs to an
organisation, and nobody can see another organisation's data. A person can belong to several organisations:
invitations to someone who already belongs to another organisation wait for them to accept (Home shows a banner),
accepting switches to the new organisation, and AgentCanvas opens the one used last.

## Roles

| Role | Can |
|---|---|
| **viewer** | see workflows and runs |
| **reviewer** | also answer approvals and human-input requests in the Inbox |
| **builder** | also build, run, test and publish workflows |
| **admin** | also manage members, policies, release gates and read the audit log |

Buttons for actions your role can't take are hidden.

## Members

![Members page](img/members.png)

Admins open **Settings › Members**, enter an email, pick a role and click **Invite**. The invitation takes effect at the person's
first sign-in with that (verified) email. Admins can change roles or remove people. An organisation can never be left without an admin who
has signed in.

To bootstrap a new install, list the first admins' emails in `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS`; they become
admins of the default organisation on first sign-in.

## Audit log

**Settings › Audit log** (admins) records every change: who did it, what it touched, when and from where. Approvals record a
hash of exactly what was approved; publishing past a failed release gate records the reason given. Rows are kept
for 400 days by default.

## Policies

![Policies page](img/policies.png)

Policies are organisation rules the editor and publishing enforce. Each policy has:

- a **scope**: *each step*, *each resource*, or *the workflow*;
- a **match** (CEL): which steps or resources it applies to, e.g. `resource.type == "model.chat"`;
- a **require** (CEL): what must hold, e.g. `resource.config.model in ["anthropic:claude-sonnet-5-5"]`;
- a **severity**: *error* or *warning*;
- optional **environments**: limit the rule to publishing to, e.g., `prod`.

Two presets get you started:

| Preset | Rule |
|---|---|
| **Writes need approval** | every step's external writes are gated by a human: `node.effects.all(e, e.effect != "external_write" \|\| e.gated)` |
| **Approved models in prod** | in `prod`, chat models must come from an approved list |

Another rule worth having when builders are not admins: no tool or remote agent may reach your private network (the
database, internal services) unless an admin adds it. Scope *each resource*, match
`resource.type in ["tool.http", "agent.a2a"]`, require
`!has(resource.config.allow_private_network) || !resource.config.allow_private_network`
(and the same with scope *each step* and `node.type == "agent.a2a"` for remote-agent steps).

Names you can use: `node` (`type`, `config`, `effects`, `ungated`, `models`, `tools`), `resource`, `workflow`,
`env`.

Admins can **Switch off** a policy without deleting it. Broken policies show in the editor's Problems dock (`E095`; `W095` for environment-only rules), steps inside
subgraphs included. Draft and debug runs still work, but **publishing** is refused until every applicable policy
holds.
