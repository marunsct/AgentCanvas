# Organisations, roles and policies

## Signing in

In production AgentCanvas signs people in through your identity provider: any OpenID Connect provider that issues
signed JWT access tokens for the AgentCanvas API (Keycloak, Okta, Entra ID, Auth0, Dex…). Google's own sign-in issues
opaque access tokens and cannot be used directly: put a broker in front (Keycloak, Auth0 or Dex with Google as its
upstream). The administrator configures it once (see [Deploy and operate](deploy.md#sign-in));
people then click through to their usual login page. Sessions renew silently. The top bar shows who you are and
your role, with **Sign out** (sign in again to use a different account). Signing out, switching account or switching
organisation also clears what the app kept in that browser tab, such as the Playground conversation, so the next
person or organisation does not see it (and it is not sent to their model).

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

## API tokens

For scripts and CI, anyone can make **personal API tokens** under **Settings › You › API tokens**: give it a name,
pick a role (at most your own) and an expiry (30 days, 90 days, a year or never), then **Create token**. The token
(`act_…`) is shown once: copy it then. Send it as `Authorization: Bearer act_…` to the API, exactly where a signed-in
browser sends its sign-in token:

```bash
curl -H "Authorization: Bearer act_…" https://<your AgentCanvas>/api/v1/projects
```

- A token acts as you, in the organisation you made it in only (`X-Org-Id` naming another one is refused), and with
  the lower of its role and your role now: demoting you demotes your tokens, removing you from the organisation
  stops them.
- Only a fingerprint (SHA-256) of the token is stored. Revoke a token there and it stops working at once; admins see
  everyone's tokens in the same place and can revoke any of them.
- A token cannot make another token, the organisation's SCIM token, invite or remove members, or change which groups
  give which roles: those need a real sign-in, so a leaked token cannot leave access behind that outlives revoking
  it.
- An admin can cap how long tokens live (**Settings › Organisation › General › Limits and policies › Longest API
  token lifetime**), and the operator can set a ceiling for every organisation (`AGENTCANVAS_MAX_TOKEN_DAYS`). With a
  cap, a new token must expire within it (**Never** is not offered); tokens made before keep their expiry, so revoke
  any that live longer.
- Every change made with a token is in the audit log under your name, with the token's id (`personal token tok_…`);
  making and revoking tokens are recorded too.

Deployment keys are different: they belong to a published workflow and can only run it (see
[Publish](publish.md)).

## Roles from groups

Where people sign in with OpenID Connect, an admin can let the identity provider decide roles. Under **Settings ›
Organisation › General › Single sign-on groups** name the claim that carries the groups (`groups` by default; a dotted
name such as `realm_access.roles` reaches a nested claim), and list which role each group gives. At every sign-in:

- the strongest role among the person's mapped groups is their role, replacing one set by hand under Members;
- someone in a mapped group who was invited joins at once, with no invitation to accept;
- someone in a mapped group who is not a member or an invitee joins only on a server with a single organisation, or
  when the operator sets `AGENTCANVAS_SSO_GROUPS_ADD_MEMBERS=true`. With several organisations one organisation's table
  must not enrol people another organisation brought (an admin could map a claim every token carries);
- a token with none of the mapped groups **keeps** the person's role (the default), or **removes** them from the
  organisation if you choose that;
- a change that would leave the organisation without an admin who has signed in is skipped, so the provider can
  never lock you out of your own organisation.

Each change is in the audit log (`sso_role_sync`, `sso_member_removed`). Clear the table to manage roles under Members
again. In Matura mode roles come from Matura and this setting is not offered.

## Provisioning from your directory (SCIM)

Where people sign in with OpenID Connect, an admin can let the identity provider keep the member list in step with the
directory over **SCIM 2.0** (Okta, Entra ID, OneLogin and others). Under **Settings › Organisation › General ›
Provisioning**, **Create token**, copy it (it is shown once) and give it to the provider as a bearer token, with the base
URL shown there (`https://<your AgentCanvas>/api/scim/v2`). **Replace token** stops the old one at once; **Switch off**
turns SCIM off.

What the provider can do:

| In the directory | In AgentCanvas |
|---|---|
| Assign a user to the app | Adds the person (matched by email, `userName`) as a member with the `viewer` role. Someone who already belongs to another organisation still has to accept, as with an invitation. |
| Deactivate a user (`active: false`) | Suspends the membership: no access and their API tokens stop, but nothing is deleted. Reactivating restores it. |
| Remove a user | Removes the membership (the account stays: they may belong to other organisations). |
| Push a group with members | Members get the role the group's **name** has in the groups table (see above); a person in several groups gets the strongest, and `viewer` when none gives one. |
| Remove a group | Its members lose the role it gave. |

Everything is limited to this organisation: the token cannot see or change another one's people. A person nobody
pushes, such as the first admin, keeps their role. Changes the provider asks for that would leave the organisation without
an admin who has signed in are refused for users (the provider shows an error) and skipped for group-driven roles.
Every change is in the audit log without a person, marked `scim`. Supported: `Users` and `Groups` with `GET` (filter
`attribute eq "value"`, `startIndex`, `count`), `POST`, `PUT`, `PATCH` and `DELETE`, and the `ServiceProviderConfig`,
`ResourceTypes` and `Schemas` documents; bulk, sorting and ETags are not. In Matura mode members come from Matura and
SCIM is off.

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
