# 18 — Identity, user administration and multi-tenancy plan (2026-10-11)

> **Update 2026-10-11:** the owner asked for the platform to be a **separate open-source repository** that others can
> use to host their own apps. The platform itself (Keycloak extensions, control plane, consoles, SDKs, operator,
> enterprise features) is now planned in its own repository, working name **Tenantry**
> ([marunsct/Platform](https://github.com/marunsct/Platform), plan in its `docs/plan/`). Read this document for the AgentCanvas side: what changes in
> AgentCanvas (§2, §8), database isolation in AgentCanvas tables (T5), and the migration of members. Where this
> document describes the platform's internals, Tenantry's plan supersedes it (its control plane is called
> `tenantryd`, not `accounts`).

> **Status: proposed, waiting for the owner's approval.** Nothing here is built yet. Decision record:
> [ADR 0019](../adr/0019-identity-and-tenancy-platform.md). Planned against `main` at v0.22.0 (`2e10fff`, after S31).

## 1. Short answer

- **Yes, it can be built once and reused by all your apps.** Identity and tenancy become a small platform next to
  AgentCanvas: **Keycloak** for sign-in (passwords, Google, GitHub, MFA, lockout, sessions, enterprise SSO later),
  our own **`accounts` service** for tenants, workspaces, per-app roles, onboarding emails, entitlements, audit and
  the two admin dashboards, and **three SDK packages** (Python, TypeScript, React UI kit) that any app imports.
  AgentCanvas is the first app to use it.
- **We do not write password or MFA code ourselves.** That is the riskiest part of "enterprise grade", and Keycloak
  already does it well; it is also already in our compose file and ADR 0010.
- **Isolation moves into the database.** Every tenant-owned table gets an `org_id` and Postgres row-level security,
  so a bug in one endpoint can no longer leak another tenant's data. Today isolation is application code only.
- **Individuals share one common tenant** ("Individuals"), and each gets a private personal workspace inside it, so
  they never see each other's work. Companies get their own tenant, and whoever onboards it becomes its owner with
  full rights to manage it.
- **8 sprints, T1–T8** (§11). Recommended slot: right after S32, before the remaining framework sprints, because
  adding `org_id` to every table gets harder with each sprint that adds tables (§13).

## 2. What exists today

| Piece | Where | Keep, change or replace |
|---|---|---|
| OIDC bearer-token check (JWKS, issuer, audience, `email_verified`) | `apps/api/agentcanvas/security/auth.py` | moves into the `platform-identity` SDK, same checks |
| `CurrentUser(id, org_id, role, memberships)`, `X-Org-Id` header, first-sign-in provisioning, `bootstrap_admin_emails` | `api/deps.py` `current_user` | same shape, filled by the SDK from the token and `accounts` |
| Org isolation: `org_scope` checks every named id, 404 on another org's id | `api/deps.py`, `tests/test_org_scope.py` | kept as layer 2; RLS becomes layer 1 |
| Roles admin / builder / reviewer / viewer, `required_role` by method and path | `api/deps.py` | kept as AgentCanvas *app roles*, registered with `accounts` |
| `organization`, `app_user`, `membership` (with `accepted` invitations) | `db/models.py` | `organization` stays as the workspace row; users and memberships move to `accounts` (migrated, then read-only, then dropped) |
| Members, invitations, audit and policy pages | `api/v1/org.py`, `api/v1/auth.py`, `web/src/app/MembersPage.tsx`, `AuditPage.tsx` | members and identity audit move to the shared admin console; org policies stay in AgentCanvas |
| Per-org data key wrapped by a master key (local, file, AWS, GCP KMS) | `security/kms.py`, `org_data_key` | kept; adds bring-your-own-key per tenant (T6) |
| Rate limits per deployment, webhook, project | `services/ratelimit.py` | adds per tenant and per user, and sign-in limits (Keycloak) |
| Audit log | `audit_log`, `api/audit.py` | kept for app actions; identity events go to `accounts`; both get hash chaining (T6) |
| SPA sign-in with oidc-client-ts, auth code + PKCE | `web/src/api/auth.ts`, `AuthGate.tsx` | wrapped by `@platform/identity` |
| Keycloak 26 in compose (profile `auth`, `start-dev`) | `infra/docker-compose.yml` | becomes a configured realm from code (T1) |

Only 5 of the 39 tables carry `org_id` (`org_data_key`, `membership`, `project`, `audit_log`,
`org_policy`). The other tenant-owned tables (about 30; `organization`, `app_user` and `rate_window` are not tenant data) reach their organisation through `project_id`, `workflow_id`, `thread_id` or similar.

## 3. Words and roles

| Word | Meaning |
|---|---|
| **Tenant** | A customer account: a company, or the one shared **Individuals** tenant. Owns admins, security policy, SSO, plan, enabled apps and its encryption key. |
| **Workspace** | Where an app keeps data; today's AgentCanvas `organization` (`org_id`). The isolation unit for RLS. A company tenant starts with one and can add more; each individual has one personal workspace. |
| **User** | One person, one Keycloak account, identified by email. Can belong to several tenants. |
| **App** | A product that uses the platform (AgentCanvas first). Registers itself with a manifest: its client id, roles and default role. |

Roles at three levels:

| Level | Roles | Who |
|---|---|---|
| Platform | `platform_admin` (everything), `platform_support` (read, and impersonate only with the tenant's consent) | you and your staff; the product admin dashboard |
| Tenant | `owner` (all rights, can delete the tenant and transfer ownership), `admin` (members, security, SSO), `member` | the person who onboards a tenant becomes `owner` |
| App, per workspace | AgentCanvas: `admin`, `builder`, `reviewer`, `viewer` (unchanged) | set by tenant admins; tenant `owner`/`admin` are app `admin` everywhere in their tenant |

## 4. Architecture

```
            Browser (AgentCanvas web, future app web, admin console)
               |  sign-in redirect (OIDC code + PKCE)           |  API calls with access token
               v                                                v
   +-----------------------+   admin API   +-------------------------------+
   | Keycloak (realm       |<--------------|  accounts service (FastAPI)   |
   |  "platform")          |   events      |  tenants, workspaces, roles,  |
   | users, passwords, MFA,|-------------->|  invites, email outbox, audit,|
   | Google/GitHub/SSO,    |               |  entitlements, SCIM, admin UI |
   | sessions, lockout     |               |  own Postgres DB "accounts"   |
   +-----------------------+               +-------------------------------+
               ^  JWKS (verify tokens)        ^  roles lookup (cached) + change webhooks
               |                              |
   +-----------+------------------------------+------+      +----------------------+
   | AgentCanvas API  (platform-identity SDK)        |      | future app API (SDK) |
   | sets app.org_id per transaction -> Postgres RLS |      +----------------------+
   +-------------------------------------------------+
```

- **Tokens.** Access tokens are short (5 minutes) and carry `sub`, `email`, `email_verified`, the Keycloak
  organization (tenant id) and `amr` (whether MFA was used). Refresh tokens rotate on every use. Apps verify tokens
  locally with JWKS; no call to Keycloak per request.
- **Roles.** Keycloak cannot express "builder in workspace A, viewer in workspace B" cleanly, so per-workspace app
  roles live in `accounts`. The SDK fetches `GET /v1/subjects/{sub}/grants?app=agentcanvas` once and caches it for
  60 s; `accounts` sends a signed webhook on any change so caches drop at once (role removal takes effect in
  seconds, not at token expiry).
- **Email.** `accounts` owns an outbox table and sends through SMTP (any provider: SES, Postmark, Mailgun, a local
  Mailpit in dev). Keycloak's own emails (verify email, reset password) use the same SMTP settings and the themed
  templates.
- **Admin console.** One SPA served by `accounts` at `/admin`, with a tenant view and a platform view, built from the
  React kit. Apps link to it ("Manage members") instead of each rebuilding member pages.
- **Where it lives.** `platform/keycloak/` (realm as code, theme), `platform/accounts/` (service),
  `platform/packages/identity-py/`, `platform/packages/identity-ts/`, `platform/packages/identity-react/`. No
  imports of `agentcanvas` from `platform/`; CI checks that. It moves to its own repository when a second app
  starts (T8 writes the extraction steps).

## 5. User journeys

**Sign up (self-service), `/signup` on the themed Keycloak page**

1. Choose: email and password, **Continue with Google**, or **Continue with GitHub**.
2. Email sign-ups verify their address by a link before first use. Google accounts arrive verified; GitHub's primary
   verified email is used, and sign-up is refused when GitHub has no verified email.
3. Question on first sign-in: *Just me* or *My organisation*.
   - *Just me*: the user joins the **Individuals** tenant as `member` and gets a personal workspace where they are
     AgentCanvas `admin`. Nobody else in Individuals can see it.
   - *My organisation*: asks for the organisation name, creates a tenant and its first workspace, and makes the user
     `owner`.
4. An email whose domain a tenant has verified (T8) is routed to that tenant's sign-in (SSO or invite-only) instead.

**Onboard someone (tenant admin or product admin), Admin console → Members → Add**

1. Enter the person's email; it becomes their user name. Pick tenant role and app roles.
2. Pick how they get in:
   - **Email a set-password link** (default, the link works once and expires in 72 hours), or
   - **Email a generated password** (what you described): 16 random characters from a secure generator, stored only
     in Keycloak as a *temporary* password, so the first sign-in forces a new one. Unused after 72 hours, it is
     disabled and has to be re-sent.
3. Optional **"Send it to a different address"**: the link or password goes to that address (for example the
   admin's own, to hand over in person). The user's own email is then unverified, and Keycloak asks them to verify
   it at first sign-in.
4. The person signs in, sets a password (and MFA when the tenant requires it), and lands in their workspace.
5. Onboarding a **new tenant** from the product admin dashboard does the same with tenant role `owner`, so that
   person has all rights to manage their tenant.

Resend, revoke and bulk add (CSV of email, role) are in the same screen. Every step is in the audit log.

**Everyday:** forgot password (email link), change password, add an authenticator app or passkey, see and sign
out other sessions, switch workspace or tenant from the user menu, leave a tenant.

**Offboarding:** removing a member revokes their grants and signs out their sessions for that tenant at once.
Deleting a tenant (owner, with a typed confirmation and a 7-day grace) exports nothing by default, then deletes the
tenant's rows and its data key, which makes any copy in backups unreadable (crypto-shredding).

## 6. Dashboards

**Tenant admin dashboard** (tenant `owner` and `admin`)

| Area | What it does |
|---|---|
| Members | list, search, add (§5), change roles per workspace and app, suspend, remove, transfer ownership |
| Invitations | pending, resend, revoke, bulk CSV |
| Workspaces | create, rename, delete; who has access |
| Security | require MFA, password policy (length, history, expiry), session idle and maximum lifetime, allowed IP ranges, allowed sign-in methods (password, Google, GitHub, SSO only) |
| Sessions | every member's active sessions; sign out one or all |
| Single sign-on (T8) | connect the company's OIDC or SAML provider, verify the email domain, map groups to roles, SCIM token |
| API keys | service accounts per workspace with scoped roles and expiry |
| Audit log | sign-ins, failures, lockouts, member and role changes, settings changes; filter and export CSV |
| Usage | apps enabled, seats, and links into each app's own usage (AgentCanvas spend page) |
| Danger zone | export tenant data, delete tenant |

**Product admin dashboard** (`platform_admin`, `platform_support`)

| Area | What it does |
|---|---|
| Tenants | list and search; create (onboards an owner, §5); suspend, reactivate, delete; plan, seat limit and quotas; which apps are enabled |
| Users | search across tenants; lock, unlock, force password reset, sign out everywhere, see their tenants |
| Individuals tenant | the common tenant's members, abuse handling (suspend a user), sign-up controls (open, invite-only, closed; blocked email domains) |
| Impersonation | `platform_support` asks, the tenant owner approves (or a break-glass reason for `platform_admin`), time-boxed to 30 minutes, banner shown, fully audited |
| Security events | lockouts, password spraying, new-country sign-ins, MFA resets, across tenants |
| Platform audit | everything platform staff did |
| Email | outbox state, bounces, resend |
| Apps | registered apps, their roles and clients |
| Feature flags | per tenant |

## 7. Isolation and security controls

| Control | How | Sprint |
|---|---|---|
| Tenant data isolation in the database | `org_id` on every tenant-owned table (backfilled, `NOT NULL`, composite FK `(org_id, parent_id)` to the parent so a child cannot point into another workspace), RLS `USING (org_id = current_setting('app.org_id'))` with `FORCE ROW LEVEL SECURITY`; API connects as `agentcanvas_app` that owns nothing; migrations run as owner | T5 |
| Context per request | SDK sets `SET LOCAL app.org_id` (and `app.tenant_id`, `app.user_id`) at the start of each transaction; works with PgBouncer transaction pooling; no context = no rows | T5 |
| Background work | runner, triggers, retention and online evaluators set the context per job from the job's own row; only migrations and the retention sweeper use a `BYPASSRLS` role, and its use is audited | T5 |
| Public entry points | share links, webhooks and deployment API keys resolve their workspace from the credential first, then set the context | T5 |
| Second layer | `org_scope` and list filters stay; the cross-tenant test suite runs every route as tenant B against tenant A's ids, once with `org_scope` disabled to prove RLS alone holds | T5 |
| Other stores | cache keys, object storage paths, workspace sandboxes, knowledge chunks and vector rows all prefixed or filtered by `org_id` | T5 |
| Encryption | per-workspace data key (exists); per-tenant bring-your-own KMS key for enterprise; TLS everywhere; secrets never in logs | T6 |
| Silo tier (later) | an enterprise tenant can get its own database through a routing table; same code | after T8 |
| Passwords | Keycloak: minimum 12 characters, not the email, last 5 not reused, breached-password check (extension), PBKDF2/argon2 hashing | T1 |
| Lockout and abuse | Keycloak brute-force detection (temporary lockout after 5 failures, permanent after repeated waves), sign-in and sign-up rate limits per IP, CAPTCHA on sign-up when abuse is detected | T1, T6 |
| MFA | TOTP and passkeys (WebAuthn); a tenant can require it; recovery codes; admin MFA reset is audited | T6 |
| Sessions | short access tokens, rotating refresh tokens, idle and maximum lifetimes per tenant, list and revoke, revocation on removal and password change | T6 |
| Least privilege | roles per workspace and app; API keys scoped and expiring; platform staff cannot read tenant data without consented impersonation | T4, T7 |
| Audit | identity events from Keycloak's event listener plus `accounts` actions; app actions in AgentCanvas `audit_log`; both append-only with a hash chain; export to a SIEM (syslog/HTTPS) | T6 |
| Enterprise SSO and provisioning | per-tenant OIDC/SAML identity provider, domain verification by DNS TXT, home-realm discovery, just-in-time accounts, SCIM 2.0 users and groups, group-to-role mapping | T8 |
| Data rights | tenant export (JSON + files), tenant deletion with crypto-shredding, user deletion | T7 |

## 8. What changes in AgentCanvas

- `api/deps.py`: `current_user` delegates to `platform_identity.fastapi.current_principal` and maps it onto the
  existing `CurrentUser`; `X-Org-Id` stays (now "workspace"). `role_policy` and `required_role` are unchanged.
- `security/auth.py`: becomes a thin re-export of the SDK's verifier (its tests move with it).
- `db/session.py`: sets the tenant context at the start of every transaction; a second engine for the bypass role.
- Migrations: add and backfill `org_id` on about 30 tables, composite FKs, RLS policies, the `agentcanvas_app` role.
- `membership`, `app_user` invitations: migrated to `accounts` by a one-off script; AgentCanvas then reads grants
  from the SDK. `app_user` stays as a local profile cache keyed by `sub` (other tables reference it).
- `api/v1/org.py` members endpoints and `MembersPage.tsx`: replaced by links into the admin console (policies,
  settings and the app audit page stay).
- `settings.py`: `auth_mode` gains `platform`; `dev` stays for local work and tests; production refuses `dev`
  (unchanged rule).
- Web: `api/auth.ts` wraps `@platform/identity`; the user menu and workspace switcher come from the React kit.
- Tests: `test_org_scope.py` grows into the RLS suite; `test_auth.py` allowlists stay.

The local dev edition (SQLite, `auth_mode=dev`, one command) keeps working; RLS applies on Postgres only.

## 9. How a second app plugs in

1. Add an app manifest (`platform/apps/<app>.yaml`: client id, redirect URLs, roles, default role) and apply it;
   this creates the Keycloak client and registers roles in `accounts`.
2. Backend: `pip install platform-identity`; add `Depends(current_principal)` and `require_role("editor")`; add
   `org_id` + RLS to its tables with the SDK's Alembic helper.
3. Frontend: `npm i @platform/identity @platform/identity-react`; wrap the app in `<IdentityProvider>`; drop in
   `<UserMenu/>` and `<WorkspaceSwitcher/>`.
4. Done: sign-up, Google/GitHub, MFA, members, tenants, admin dashboards and audit all work for the new app.

T8 proves this with a tiny sample app in `platform/examples/hello-app`.

## 10. Defaults I picked where the ask forks

| Question | Default | Why |
|---|---|---|
| Identity provider | Keycloak 26 (self-hosted), one realm | already wired in; covers MFA, social, SSO; no per-user fees for free individuals |
| How onboarded users get in | set-password link by default; generated temporary password as the option you asked for, forced change on first sign-in | a password in an email can be read later by anyone with mailbox access; a one-time link cannot be reused |
| Individuals | one shared **Individuals** tenant, a private personal workspace each | your "one common tenant", without individuals seeing each other's work |
| Who manages a new tenant | the onboarded email becomes tenant `owner` | "they will have all the rights to manage their tenant" |
| Isolation model | shared database, RLS per workspace; dedicated database for enterprise later | strong, cheap, one code path |
| Login pages | Keycloak hosted pages themed with our tokens (Keycloakify), light and dark | never handle passwords in app code; one look for all apps |
| Email | SMTP outbox in `accounts`, Mailpit in dev | provider-neutral |
| Code location | separate open-source repository (Tenantry), owner's decision 2026-10-11 | reusable by other apps and other people |
| Sign-up policy | open for Individuals; company tenants also self-serve; product admin can switch to invite-only | matches "enable user creation" |
| Existing data | `org_default` becomes the first company tenant; current members keep their roles | no break for current installs |

## 11. Sprint plan

Sprint names use a separate track (T1–T8) so they do not renumber the framework sprints S33–S36. Each sprint ends
like the others: tests green, docs, CHANGELOG, HANDOVER updated, merged.

| Sprint | Goal | Main work | Exit (done when) |
|---|---|---|---|
| **T1 Platform foundation** | the pieces exist and talk | `platform/` layout and boundary check in CI; Keycloak realm as code (Organizations on, password policy, brute-force, Google and GitHub providers with placeholder secrets, required actions, SMTP to Mailpit); `accounts` service skeleton (FastAPI, own DB and migrations, tenants, workspaces, memberships, app registry, grants API, signed webhooks); `platform-identity` Python SDK (verify, principal, grants cache); compose profile `platform`; ADR 0019 accepted | `make platform-up` starts Keycloak + accounts; a token from the realm passes the SDK; grants come back for a seeded user |
| **T2 Sign up and sign in** | anyone can create an account | Keycloakify theme (light/dark, our tokens) for sign-in, sign-up, verify email, forgot password, update password, MFA pages; Google and GitHub; first-sign-in choice *Just me* / *My organisation*; Individuals tenant and personal workspaces; `@platform/identity` TS package; AgentCanvas `auth_mode=platform`, web sign-in through the package, user menu | Playwright: sign up with email (Mailpit link), sign in, sign out, forgot password; Google and GitHub tested against a mock OIDC provider; a new individual lands in their own workspace |
| **T3 Onboarding by email** | admins add people by email | add member with set-password link or generated temporary password, optional other address, forced change on first sign-in, 72-hour expiry; email outbox with retries and templates; resend, revoke, bulk CSV; new-tenant onboarding with owner; migration of AgentCanvas `membership` and invitations into `accounts` | Playwright: admin adds a user by temporary password, the user is forced to change it, lands with the right role; existing members keep access after migration |
| **T4 Tenant admin dashboard** | tenants manage themselves | admin console SPA at `/admin` (tenant view); `@platform/identity-react` kit (members table, invite dialog, role picker, workspace switcher, sessions list); members, invitations, workspaces, API keys, audit view, usage links; AgentCanvas members page replaced by the kit and links | a tenant owner can do every row of §6 tenant table except Security, SSO and Danger zone; AgentCanvas shows the shared user menu and switcher |
| **T5 Tenant isolation in the database** | the database refuses cross-tenant access | `org_id` on about 30 tables with backfill and composite FKs; RLS with `FORCE`; non-owner app role; per-transaction context in AgentCanvas and `accounts`; worker and public-route context; cache, storage, sandbox and knowledge prefixes; per-tenant and per-user rate limits; cross-tenant suite over every route (with `org_scope` off too) | the cross-tenant suite passes with `org_scope` disabled; a raw `SELECT` as the app role without context returns no rows; performance within 10% on the k6 load test |
| **T6 Enterprise security** | the controls an enterprise review asks for | MFA (TOTP, passkeys, recovery codes) and tenant "require MFA"; session list and revoke, idle and maximum lifetimes; tenant security settings page; IP allow-lists; breached-password check; audit hash chain and SIEM export; Keycloak events into `accounts`; bring-your-own KMS key per tenant; security checklist (OWASP ASVS L2) run and recorded | a tenant with "require MFA" cannot use the app without it; revoking a session ends API access within a minute; tampering with an audit row is detected |
| **T7 Product admin dashboard** | you can run the business | platform view of the console: tenants lifecycle, plans and quotas, enabled apps, user search and lockout tools, Individuals sign-up controls, consented impersonation, security events, platform audit, email outbox, feature flags; tenant export and deletion with crypto-shredding | every row of §6 product table works; an impersonation needs approval, shows the banner, ends after 30 minutes and is in both audit logs |
| **T8 SSO, SCIM and second app** | enterprises bring their own directory; reuse is proven | per-tenant OIDC/SAML provider, DNS domain verification, home-realm discovery, just-in-time accounts, SCIM 2.0 (users, groups), group-to-role mapping; app manifest tooling; `platform/examples/hello-app` using only the SDKs; extraction guide for moving `platform/` to its own repository | sign-in through a test SAML and OIDC provider; SCIM create/disable reflected within a minute; hello-app gets sign-up, roles and admin pages with no copied code |

T5 does not depend on T2–T4 and can start right after T1 if you want isolation first.

## 12. Risks

| Risk | Mitigation |
|---|---|
| RLS migration touches every table and every query path | composite FKs and backfill in one migration, verified on a copy of a real database; the cross-tenant suite; feature flag to run the app role with RLS in report-only mode (log, do not block) for one sprint |
| Keycloak Organizations or theming hits a limit | the SDK hides Keycloak from apps; Zitadel is the fallback named in ADR 0019 |
| Role caches lag after a change | webhook invalidation plus 60 s cache; removal also revokes sessions |
| Emails land in spam | SPF, DKIM and DMARC set up on the sending domain before T3 goes live; outbox shows bounces |
| Generated passwords in email | 72-hour expiry, forced change, link mode as default, audit of every send |
| More to operate | compose and Helm charts for Keycloak and `accounts` (Postgres for both), health checks, backups in the deploy kit |
| Abuse of open sign-up for Individuals | email verification, rate limits, CAPTCHA on abuse, blocked domains, product admin suspend |

## 13. Decisions for the owner

1. **Approve the approach** (Keycloak + `accounts` + SDKs, tenant → workspace, RLS). Recommended.
2. **Where T1–T8 go:** *after S32, before S33–S36* (recommended: the RLS migration grows with every new table), or
   after S36, or alternating with framework sprints.
3. **Onboarding default:** set-password link (recommended) or generated password emailed.
