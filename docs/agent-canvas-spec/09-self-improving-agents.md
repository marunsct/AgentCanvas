# 09 — Self-Improving Agents (Learning Loops)

"Self-improving" is not one feature; it is a **ladder of learning mechanisms** with very different speed, risk and cost. AgentCanvas makes each rung a visual, governed, reversible building block built on the persistence layers of doc 08 and on LangSmith (feedback, datasets, experiments, prompts/Context Hub).

## 1. The learning ladder

| Rung | What changes | Speed | Risk | Mechanism (ecosystem) | Where it's stored |
|------|--------------|-------|------|------------------------|-------------------|
| **R1 Memory** | What the agent *knows* about users/org/world | Instant (hot path) or nightly | Low–medium (memory poisoning) | Store memory tools, Deep Agents memory files, LangMem-style memory managers, background consolidation agent | L2 Store / L3 files |
| **R2 Examples** | How the agent *behaves on similar inputs* (few-shot) | Minutes | Low | Curated trajectories → example bank → dynamic few-shot selection middleware | L2 Store (`examples` space) + LangSmith dataset |
| **R3 Skills** | *Procedures* the agent can load | Hours | Medium | Deep Agents writable skills (write approval), skill library review | Store / Context Hub / Git |
| **R4 Prompts** | The agent's *instructions* | Hours–days | Medium–high | Prompt optimisation from feedback (LangMem `create_prompt_optimizer`: `prompt_memory` / `metaprompt` / `gradient`), human edit suggestions | LangSmith prompts / Context Hub commits |
| **R5 Routing & config** | Which model/prompt/tool-set variant serves which traffic | Days | Medium | A/B assistants, multi-armed bandits over variants scored by feedback/online evals | Assistant configs |
| **R6 Weights** | The *model* itself | Weeks | High | Fine-tuning on curated trajectories (LangSmith **Smithtune**, beta → Fireworks/Baseten), distillation to smaller models | Model registry refs |

Principle: **every rung writes a versioned artifact, and every artifact passes a gate** proportional to its risk before it influences production (R1 guarded writes; R2–R6 evaluation gate + optional human approval + canary).

## 2. Signals — what the agent learns from

| Signal | Captured by | Notes |
|--------|------------|-------|
| Explicit feedback | Chat widget 👍/👎 + comment; API `POST feedback` (LangSmith feedback on run id, presigned feedback tokens for end users) | Attached to trace + canvas node |
| **HITL edits** | Approval node / `interrupt_on` "edit" & "reject" decisions (the diff between proposed and approved tool args) | Highest-quality supervised signal; captured automatically |
| Corrections in conversation | Classifier middleware detects "that's wrong, it should be …" | Optional |
| Outcome events | Business events via event handlers (ticket reopened, deal won, refund issued) joined to thread id | Delayed reward |
| Online evaluators | LLM-judge / code / composite scores on sampled production runs | Continuous |
| Rubric verdicts | `RubricMiddleware` grading iterations | Per run |
| Offline experiments | Dataset runs | Gate + regression |

**Feedback Schema designer** (FR-LRN-01): define feedback keys (e.g. `correctness` 0–1, `tone` enum, `resolution` bool), who can give them, and how they map to rewards. All signals are normalised to `(run_id, thread_id, canvas_node_id?, key, score, comment, source, actor)`.

## 3. Learning Loop nodes (canvas)

Learning loops are ordinary workflows (usually cron- or event-triggered) built from specialised nodes, so they're visible, testable and versioned like everything else.

| Node | Function | Pri |
|------|----------|-----|
| **Feedback Source** (trigger/resource) | Stream/batch of feedback & outcome events with filters (workflow, version, key, score range, time window) | P1 |
| **Trace Query** | Select runs/trajectories from LangSmith by trace-query filter, feedback, or judge | P1 |
| **Memory Extractor** | LLM extraction of facts/preferences/episodes from threads into memory spaces with dedup/merge (LangMem memory-manager pattern or a Deep Agent consolidator) | P1 |
| **Example Curator** | Turn good trajectories (high score or approved-without-edit) and corrected ones (HITL edits applied) into examples; dedup by embedding; balance by category | P1 |
| **Few-shot Selector** (middleware) | At inference, retrieve k most similar examples from the `examples` space and inject into the prompt (budget-aware) | P1 |
| **Prompt Optimizer** | Proposes a new prompt version from trajectories + feedback (algorithms: `prompt_memory`, `metaprompt`, `gradient`; multi-prompt optimisation for multi-agent systems), outputs a diff + rationale | P1 |
| **Skill Writer** | Drafts/updates `SKILL.md` procedures from repeated successful trajectories; requires approval | P2 |
| **Variant Router** (bandit) | Thompson-sampling/ε-greedy between assistant variants using reward keys; guardrails on min traffic and max regret | P2 |
| **Fine-tune Job** | Launch Smithtune-style fine-tuning from a curated dataset; register candidate model | P2 |
| **Eval Gate** | Runs the gating experiment(s) on candidate vs current; pass/fail thresholds; statistical significance | P1 |
| **Human Review** | Review card showing the candidate artifact diff (memory items, prompt diff, skill diff), eval comparison, sample traces | P1 |
| **Promote / Canary / Rollback** | Publish the artifact (prompt commit tag, memory batch, example batch, assistant config) to an environment with traffic percentage; auto-rollback on online-eval drop | P1 |

### 3.1 Template: "Nightly self-improvement loop"

```
[Cron 02:00] → [Trace Query: last 24h, support_triage@prod, feedback.correctness < 0.6 OR hitl.edited]
      → [Example Curator] ─────────────────────────────► [Remember → examples space (pending)]
      → [Prompt Optimizer: target prompt "reply_drafter.system"]
      → [Eval Gate: dataset support-regression v12, metric correctness ≥ current + 0.02, no key drops > 3%]
          ├─pass→ [Human Review (owner: support-ai team)] → [Promote: prompt tag 'prod' canary 10% → 100% after 24h]
          └─fail→ [Emit event learning.rejected] → [Slack notify]
```

### 3.2 Template: "Hot-path personal memory"
Deep Agent with `/memories/` routed to a per-user Store space; memory tools with approval for sensitive categories; nightly consolidation workflow merging and decaying facts.

## 4. Functional requirements

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-LRN-01 | Feedback schema designer & capture in chat widget/API/inbox; HITL edits recorded as feedback automatically. | P1 |
| FR-LRN-02 | **Learning Center** page per workflow: signals over time, active learning loops, pending candidate artifacts, history of promotions/rollbacks, impact charts (score before/after). | P1 |
| FR-LRN-03 | Every learned artifact is **versioned** with provenance (source runs, algorithm, model used, reviewer), **diffable**, and **one-click revertible**. | P1 |
| FR-LRN-04 | Gates are mandatory for R2–R6 promotions to prod; configurable for R1 (e.g. auto-apply to per-user memory, gate for org memory). | P1 |
| FR-LRN-05 | Prompts referenced by nodes can be bound to a **prompt tag** (e.g. `prod`) in LangSmith/Context Hub so promotions don't require a redeploy; the canvas shows the resolved version. | P1 |
| FR-LRN-06 | Learning budget per workflow (tokens/$ per day for extraction/optimisation/eval) with alerts. | P1 |
| FR-LRN-07 | "Explain learning": for any response, show which memories, examples, skills and prompt version influenced it (recorded as trace metadata). | P2 |
| FR-LRN-08 | Safety: memory/example writes from untrusted inputs are quarantined until reviewed or auto-screened; PII scrubbing before examples/datasets; org-level artifacts need admin approval; opt-out per end user ("don't learn from my data"). | P1 |
| FR-LRN-09 | Drift & regression watch: online evals compare pre/post promotion; auto-rollback rules. | P2 |
| FR-LRN-10 | Multi-agent credit assignment: feedback on a final answer is attributed to sub-agents/nodes via trajectory evaluation to target the right prompt. | P2 |

## 5. Technical design

- **Learning Service** (control plane) orchestrates loops as Temporal workflows *or* as AgentCanvas workflows deployed to a dedicated "learning" assistant (dogfooding; recommended — same observability/eval stack).
- Artifacts:
  - Prompts → LangSmith prompt commits / Context Hub (tags `candidate`, `prod`); nodes reference `prompt://reply_drafter.system@prod`; the runtime resolves and caches with TTL; trace metadata records the commit hash.
  - Examples → Store namespace `("workflow", <id>, "examples")` with vector index on input; mirrored to a LangSmith dataset for evaluation.
  - Memories → Store namespaces with provenance fields `{source_run, source_thread, extracted_by, confidence, status: active|pending|quarantined}`.
  - Skills → Context Hub backend or Git (PR opened by the loop for review).
  - Model candidates → registry entry `{base, provider, job_id, dataset_version, eval_experiment}`; switching models is an assistant config change behind the gate.
- **Gate evaluation**: experiments run candidate vs baseline on pinned dataset versions with repetitions; significance via bootstrap CI; results posted to the review card.
- **Canary**: two assistants (baseline/candidate) behind the published endpoint with weighted routing in the Run Orchestrator; online evaluators tag by variant.
- Upstream note: the LangMem SDK provides memory managers and prompt optimisers; AgentCanvas wraps these behind stable node interfaces so the implementation can be swapped (LangMem, Deep Agents consolidation agent, custom) as the ecosystem evolves.

## 6. Risks specific to self-improvement

| Risk | Mitigation |
|------|-----------|
| Memory poisoning / prompt injection persisting across sessions | Untrusted tagging, quarantine, read-only shared memory, write approvals, injection classifiers, provenance & bulk revert |
| Reward hacking (optimising 👍 at the expense of correctness) | Multi-metric gates, held-out datasets, human review for prompt changes |
| Privacy (learning from user data) | Consent flags, PII scrubbing, per-tenant isolation of all learned artifacts, erasure propagates to examples/memories/datasets |
| Silent drift | Version pinning, trace metadata of artifact versions, drift dashboards, auto-rollback |
| Cost | Learning budgets, sampling, cheaper models for extraction |
