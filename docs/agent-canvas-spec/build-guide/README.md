# AgentCanvas Build Guide — Start Here

This guide turns the specification (docs 00–13) into **step-by-step instructions**. It is written so that a developer who has never built an agent platform can build AgentCanvas by following it in order.

> **Rule of thumb:** the *spec* (docs 01–12) says **what** to build and why. This *build guide* says **how**, in which order, and how to know you are done. When they disagree, the review report ([13-deep-review-report.md](../13-deep-review-report.md)) records the decision, and this guide follows it.

## 1. What you will build

A web app where people drag widgets onto a canvas and connect them to make AI workflows. Behind the canvas:

```
Canvas (React) ──JSON──► Graph IR ──► Validator ──► Interpreter ──► LangGraph run ──► live events back to the canvas
                                   └─► Compiler ──► readable Python project (export / deploy)
```

A **working miniature of this whole loop already exists** in [`reference/`](./reference/). It is tested end-to-end. You will study it, run it, then grow it milestone by milestone into the full product.

## 2. Reading order

| Step | Read | Time | You can now… |
|-----:|------|------|-------------|
| 1 | [00-concepts-primer.md](./00-concepts-primer.md) | 2–3 h | explain LLM, tool call, agent, graph, state, reducer, checkpoint, interrupt, stream, IR |
| 2 | [01-environment-setup.md](./01-environment-setup.md) | 1–2 h | run Postgres/Redis locally and install the toolchain |
| 3 | [03-walking-skeleton-tutorial.md](./03-walking-skeleton-tutorial.md) | 1 day | run, read and modify the reference implementation |
| 4 | [02-repository-and-conventions.md](./02-repository-and-conventions.md) | 1 h | set up the real monorepo, follow coding/git rules, know the ADRs |
| 5 | [04-implementation-plan.md](./04-implementation-plan.md) | reference | pick the next ticket; each has steps and acceptance criteria |
| 6 | [05-backend-guide.md](./05-backend-guide.md) | reference | database schema, API contracts, services, runner, persistence |
| 7 | [06-frontend-guide.md](./06-frontend-guide.md) | reference | app structure, canvas, inspector forms, run overlay, inbox |
| 8 | [07-compiler-and-node-types.md](./07-compiler-and-node-types.md) | reference | add a new node type end-to-end (recipe) |
| 9 | [08-testing-and-quality.md](./08-testing-and-quality.md) | reference | write the right tests, use fakes correctly, set up CI |
| 10 | [09-deployment-and-operations.md](./09-deployment-and-operations.md) | reference | package, deploy, observe, back up |
| 11 | [10-security-checklist.md](./10-security-checklist.md) | reference | pass security review |
| 12 | [11-troubleshooting-faq.md](./11-troubleshooting-faq.md) | when stuck | fix known problems fast |
| 13 | [12-traceability.md](./12-traceability.md) | reference | see which ticket implements which requirement |

## 3. Suggested team roles (a beginner can take any of these)

| Role | Starts with | Owns |
|------|-------------|------|
| Backend dev | M0, M1 backend tickets | IR, validator, runner API, database |
| Compiler/runtime dev | M1, M3 | node library, interpreter, code generator, events |
| Frontend dev | M2 | canvas, inspector, run overlay, inbox |
| Full-stack / DevOps | M0, M8 | local infra, CI, packaging, deployment |

A single developer can do everything in milestone order; expect roughly 6–9 months to M6 (MVP) working alone, 3–4 months with four people.

## 4. Definition of Done (applies to every ticket)

- [ ] Code follows [02 §4 conventions](./02-repository-and-conventions.md#4-coding-conventions); `ruff`, `pyright`, `tsc`, `eslint` pass.
- [ ] Tests written as the ticket specifies, all tests pass locally and in CI.
- [ ] Acceptance criteria of the ticket demonstrated (screenshot or test output in the PR).
- [ ] Docs updated if behaviour or API changed (this guide or the spec).
- [ ] Reviewed and approved by one other person.

## 5. Glossary quick reference

See the full glossary in [00-concepts-primer.md §10](./00-concepts-primer.md#10-glossary) and the product glossary in the [spec README](../README.md#6-glossary).
