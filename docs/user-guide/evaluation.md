# Evaluation Studio

Evaluation answers "is this version better than the last one?" with numbers instead of impressions. Open it from
the editor's **Evaluate** link. It has six tabs: **Datasets**, **Evaluators**, **Experiments**, **Release gates**,
**Production** and **Annotation queues**.

![Evaluation page](img/evaluation.png)

## Datasets

A dataset is a list of examples: an input, optionally a reference (expected) output, and metadata. **Create
dataset**, then fill it by:

- **Add example** by hand,
- **Add the saved test cases** of the workflow,
- adding past runs (from the run list or an annotation queue),
- **Import CSV or JSONL**.

Every change creates a new **version**, so an experiment always knows exactly which examples it used. Examples
can carry a *split* (e.g. `train`, `test`) to evaluate a subset. Datasets can be pushed to or pulled from
LangSmith when you ask (project secret or server `LANGSMITH_API_KEY`).

## Evaluators

An evaluator scores one run from 0 to 1, passes or fails it at a threshold (*pass at*), and can add a comment.

| Kind | Scores |
|---|---|
| **exact match** | the output equals the reference |
| **regex** | the output matches a pattern |
| **JSON Schema** | the output is valid against a schema |
| **LLM judge** | a model grades the output against your rubric |
| **tool trajectory** | the agent called the expected tools (in order, in any order, or a subset) |
| **workspace code** | your own function in `scripts/` |
| **composite** | a weighted mix of other evaluators, or all/any must pass |
| **pairwise judge** | which of two answers is better (asked twice with the answers swapped to avoid position bias); reports a win rate with a confidence interval |

**Try** runs an evaluator on a sample before you rely on it.

## Experiments

An experiment runs one or more **targets** over a dataset version and scores every result. A target is the draft,
a published version, or a variant with another model or prompt, and it can run **on another framework** (convert
and compare: pick *on OpenAI Agents SDK* for a target, or click **+ Compare on the other frameworks**; see
[Choosing a framework](frameworks.md#convert-and-compare)). **▶ Run experiment** starts it in the background; it can
be cancelled and survives a server restart.

The results grid shows mean scores, pass rates, latency, tokens and cost per target, and each example's output
with its run timeline one click away. The AI judges' own cost (LLM and pairwise judges) is shown apart from the
runs' cost; a judge model without a catalog price is named instead of being counted as free. **Compare** two experiments to see which examples improved or regressed.

**Simulated users:** for chat workflows, an experiment can hold a whole conversation per example with a simulated
user (a model given a persona, a goal and a turn limit). Evaluators see the full transcript.

## Release gates

An admin can require an evaluation before anything is published to an environment: minimum mean scores on a
dataset, and the maximum drop in pass rate against the version currently live. Publishing to that environment
evaluates the exact workflow being published (or reuses an evaluation of the same thing) and is refused on a
miss. Admins can publish anyway with a reason, which the audit log keeps. A gate measures with the dataset version
and evaluator settings it was saved with.

## Production (online evaluation)

Attach evaluators to a **deployment** with a sampling rate (0-100 %) and a filter (run status, metadata the
caller sent, e.g. `tier=gold`). Sampled runs are scored in the background after the response is sent, so callers
never wait. Scores appear on runs and as hourly or daily averages on the **Production** tab; the judge's own cost is
counted separately (when the judge model has no catalog price, the score's comment says its cost is not counted).

**Drift alerts** fire when an evaluator's recent mean falls below a value (*alert below*) or drops more than a set
amount under its 7-day average (*alert drop*). An alert resolves when the rule holds again, or when the window has
fewer scores than the minimum (traffic stopped: nothing is known to be wrong any more).

## Annotation queues

A queue collects runs for people to review. Fill it automatically by rules (a low online score such as
`polite < 0.7`, a failed run, a thumbs-down from an API caller) or by hand. Reviewers see input, output and
timeline, score each rubric criterion, comment, and can add the run to a dataset with a corrected reference in one
step (**Submit review**). The tab shows progress and agreement between reviewers.

API callers send thumbs up or down with `POST /v1/deployments/{id}/runs/{run_id}/feedback`.
