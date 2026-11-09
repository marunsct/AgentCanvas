---
name: write-tests
description: Turn what the workflow must do into test cases, run them, and fix what fails. Use when the person asks for tests, to check the workflow, or after building from a brief with success criteria.
---

# Writing tests

- `propose_tests` with a list of `{"name", "input", "expect"}`. `input` uses the workflow's input fields. `expect`
  checks `status` ("success", "interrupted" for a pause, or "error"), `channels` (exact final values of fields)
  and `route` (the step ids that must run, and no others). Keep each test about one behaviour, with a name that
  says it ("urgent tickets need approval").
- Before proposing, try each with `run_test` (add `mocks`, a model's name mapped to scripted replies, when no model key is set, so the test
  checks the workflow's logic, not the model). A failing test is either a bug to fix or a wrong expectation: say
  which.
- Tests are saved when the person accepts the proposal; they run before publishing.
