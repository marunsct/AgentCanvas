---
name: debug-a-run
description: Find why a workflow run failed or gave a wrong answer, explain it in plain words, fix it and prove the fix. Use when the person asks why something failed, why the output is wrong, or to fix a broken workflow.
---

# Debugging a run

1. Look before guessing: `get_problems` (validation errors and best-practice suggestions), then `list_runs` and
   `get_run` on the failed or suspicious run. `get_run` shows each step's input and output and the error.
2. Name the cause in one or two sentences: which step, what went wrong, why. Common causes:
   - E001: an AI step has no model plugged in (connect a `model.chat` to its `model` port).
   - E023 / E021: a step reads a data field that no step writes, or before it is written; fix the name or the order.
   - E013: a router condition is not valid CEL (`state.urgency == "high"`, strings in double quotes).
   - E010 / E015: no path to Finish, or a dead end; connect the step onward.
   - A model error (401, 429, timeout): a missing or wrong key (Settings > Secrets), a rate limit, or a model
     without a backup (`fallbacks` on the model).
   - A wrong answer with no error: the prompt does not say the format, or the step reads the wrong field.
3. When asked to fix it: `propose_changes` with the smallest change, then `reproduce_run` on the failed run to show
   the fix works on the same input (or `run_test` with an expectation). Say whether it worked.
4. When asked only to explain, change nothing.
