# 0019 — Identity and tenancy are a reusable platform, AgentCanvas is its first consumer

Status: proposed · Date: 2026-10-11 · Supersedes: [0010](./0010-oidc-single-org-mvp.md) once accepted

Plan and sprints: [doc 18](../agent-canvas-spec/18-identity-and-tenancy-plan.md).

## Context

AgentCanvas signs people in with any OIDC provider (Keycloak locally) and keeps organisations, memberships and
invitations in its own tables (`organization`, `app_user`, `membership`). Isolation between organisations is done in
application code: `org_scope` in `api/deps.py` checks every id a request names, and list endpoints filter by
`org_id`. Only 5 of 39 tables carry `org_id`; the other tenant-owned tables reach it through `project_id`, `workflow_id` or `thread_id`.

The owner wants: sign-up with email, Google and GitHub; onboarding where an email becomes the user name and a
generated password is emailed to it (or to another address); a tenant admin dashboard; a product admin dashboard;
individual users in one common tenant; enterprise-grade isolation and security; and all of this reusable by future
apps, not only AgentCanvas.

## Decision

1. **Do not write our own password, MFA or session code.** Run **Keycloak** (already in our compose file and ADR 0010)
   as the identity provider for every app: one realm (`platform`), one OIDC client per app, Keycloak
   *Organizations* for tenants, Google and GitHub as brokered identity providers, built-in brute-force lockout,
   password policy, TOTP and WebAuthn (passkeys), required actions (forced password change) and per-tenant
   enterprise IdPs (OIDC/SAML) later.
2. **Build one small service of our own, `accounts`**, for what Keycloak does not model well and every app needs:
   tenants and their workspaces, per-app roles inside a tenant, onboarding and invitations, the email outbox,
   entitlements (which apps and plan a tenant has), the identity audit log, SCIM, and the two admin dashboards. It
   drives Keycloak through its admin API; Keycloak stays the source of truth for credentials and sessions,
   `accounts` for tenancy and roles.
3. **Apps plug in through three packages**: `platform-identity` (Python: FastAPI dependency that verifies the token,
   resolves tenant, workspace and app roles, and sets the database tenant context), `@platform/identity` (TypeScript:
   sign-in, token refresh, current user) and `@platform/identity-react` (shared UI: user menu, workspace switcher,
   members table, invite dialog, security settings). The login and sign-up pages themselves are Keycloak pages
   themed with our design tokens (Keycloakify), so every app gets the same screens.
4. **Tenant → workspace.** A *tenant* is the customer account (a company, or the shared **Individuals** tenant). It
   owns admins, security policy, SSO, plan and its encryption key. A *workspace* is today's AgentCanvas
   `organization` and stays the data-isolation unit. A company tenant starts with one workspace; every individual
   gets a private personal workspace inside the Individuals tenant, so individuals share one tenant without seeing
   each other's data.
5. **Isolation is enforced by the database, not only by code.** Every tenant-owned table gets a non-null `org_id`
   (workspace) with composite foreign keys to its parent, and Postgres **row-level security** with `FORCE ROW LEVEL
   SECURITY`; the API connects as a role that does not own the tables and sets `app.org_id` per transaction.
   `org_scope` stays as a second layer. Enterprise tenants can be moved to a dedicated database later (silo tier)
   behind the same code.
6. **Separate open-source repository.** The platform is its own Apache-2.0 project, working name Tenantry
   (`marunsct/tenantry`), so other apps and other people can host it. AgentCanvas consumes it through the SDKs.
   (Changed on 2026-10-11 at the owner's request; the first draft kept it under `platform/` here.)

## Alternatives considered

- **Hosted identity (Auth0, Clerk, WorkOS, Cognito).** Fastest start and polished UI, but per-user pricing grows
  with free individual sign-ups, tenancy and data residency are on the vendor's terms, and self-hosted enterprise
  installs (a stated AgentCanvas edition) would still need another path. Rejected; the SDK boundary keeps the door
  open.
- **Our own identity service from scratch (FastAPI + passlib + pyotp).** Full control, but we would own password
  storage, MFA, recovery, social login, SAML and session revocation, each a security product in itself. Rejected.
- **Zitadel instead of Keycloak.** Native multi-tenancy and a modern login UI; a credible choice. Keycloak wins here
  because it is already wired in (ADR 0010, `infra/docker-compose.yml`, the SPA's oidc-client-ts), has the larger
  enterprise footprint (SAML, LDAP, federation) and Organizations since 26.x. Revisit if Keycloak theming or
  Organizations block a sprint.
- **Realm per tenant.** Strong isolation inside Keycloak but realms are heavy (hundreds, not tens of thousands),
  and a user in two tenants needs two accounts. Rejected for one realm + Organizations.
- **Personal tenant per individual** (GitHub-style). Equally isolated, but the owner asked for one common tenant,
  and one tenant is simpler to administer and bill. The workspace layer gives the same data isolation.
- **Isolation in application code only (today).** One missed filter leaks data; RLS makes the database refuse it.

## Consequences

+ Every future app gets sign-up, social login, MFA, sessions, tenants, roles, admin dashboards and audit by adding a
  client, an app manifest and the SDKs.
+ Cross-tenant reads become impossible at the database even with an application bug.
+ Enterprise asks (SSO, SCIM, enforced MFA, BYOK, audit export) have a planned home.
− Two more services to run (Keycloak, `accounts`) and one more database; the local dev edition keeps `auth_mode=dev`
  and SQLite without RLS so local work stays one command.
− A migration that adds `org_id` to about 30 tables and moves memberships out of AgentCanvas.
− Keycloak upgrades and theme maintenance become our job.
