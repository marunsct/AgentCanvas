# Architecture Decision Records

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](./0001-ir-single-source-of-truth.md) | The Graph IR is the single source of truth | accepted |
| [0002](./0002-python-backend-and-generated-code.md) | Python 3.12 backend; generated agent code is Python | accepted |
| [0003](./0003-own-runner-for-mvp.md) | MVP runtime is our own open-source runner | accepted |
| [0004](./0004-generated-code-calls-node-library.md) | Generated code calls the shared node library | accepted |
| [0005](./0005-cel-and-sandboxed-jinja.md) | CEL for conditions, sandboxed Jinja for templates | accepted |
| [0006](./0006-react-flow-zustand-tanstack.md) | React Flow canvas, Zustand and TanStack Query | accepted |
| [0007](./0007-sse-over-post.md) | SSE over POST for run streams | accepted |
| [0008](./0008-canonical-events-ag-ui.md) | Canonical run events are AG-UI based | accepted |
| [0009](./0009-migrations-ids-errors.md) | Alembic migrations, prefixed ULIDs, problem+json errors | accepted |
| [0010](./0010-oidc-single-org-mvp.md) | OIDC authentication, single organisation in the MVP | accepted |
| [0011](./0011-framework-neutral-core.md) | Framework-neutral core with an adapter boundary | accepted |
| [0012](./0012-one-repository.md) | Spec and product live in one repository | accepted |
| [0013](./0013-server-runtime-factory-hosting.md) | LangSmith Deployment may host interpreted mode via a ServerRuntime graph factory | accepted |
| [0014](./0014-no-hard-langmem-dependency.md) | No hard dependency on langmem | accepted |
| [0015](./0015-async-node-callables.md) | Node-library callables are always async | accepted |
| [0016](./0016-delta-channel-storage-mode.md) | DeltaChannel is a channel storage mode | accepted |
| [0017](./0017-stack-2026-09.md) | Stack stack-2026.09 | accepted |
| [0019](./0019-identity-and-tenancy-platform.md) | Identity and tenancy are a reusable platform; AgentCanvas is the first consumer | proposed |

Template and rules: [build-guide/02 §6](../build-guide/02-repository-and-conventions.md#6-architecture-decision-records).
