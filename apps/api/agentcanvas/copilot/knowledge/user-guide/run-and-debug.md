# Running and debugging

## Run a workflow

Open the **Run** tab (or click **▶ Test** in the toolbar).

- **JSON** mode: edit the input (pre-filled with the workflow's input channels) and click **▶ Run**.
- **Chat** mode: for workflows built around the `messages` channel. Type a message and **Send**; each message
  continues the same conversation (thread).
- **continue thread**: tick it to run on the previous thread, keeping its state, instead of starting fresh.
- **Dry run (stub risky tools)**: every tool that can write, send, execute or spend is replaced by a recorder, so
  the run has no side effects. The output shows what would have been called.

While a run streams, each card shows its status (running, *done*, interrupted, failed) and, when finished, its
tokens and time. The Run tab shows streamed output, the run's totals (tokens, latency, cost when the model has
catalog prices), and the final state as JSON.

## Human decisions

When a run reaches an **Ask for approval** or **Ask a person** step, or an agent tool call that needs approval, it pauses
and the Run tab shows **Approval needed** with the value under review.

![A run waiting for approval](img/approval.png)

- **Approve** or **Reject**. Approvals can also edit the value before approving.
- Paused runs are durable: you can close the browser or restart the server and resume later.
- The **Inbox** (top bar) lists every pending decision across all workflows you can see, so reviewers don't need
  to open the editor. Reviewers (and above) can answer.

## Breakpoints

Select a step and click **○ Add breakpoint** in the Inspector. A run pauses *before* that step
(*Paused at a breakpoint before …*); inspect the state, then **⏵ Continue**.

## Timeline: time travel and fork

The **Timeline** tab in the bottom dock lists every checkpoint of the run's thread. Select one to see the state
at that moment, including **lineage** (which step or input last wrote each channel). Edit the state and click
**⑂ Fork from here** to re-run from that point with the change, without touching the original run. This is the
quickest way to try "what if the classifier had said *high*?".

## Explain

The **Explain** tab narrates the run in plain language: which step ran, what each router chose and why
(*chose route 'normal' → reply_drafter*), where it paused. Useful for sharing what happened with someone who
doesn't read JSON.

## Pins and mocks

- **📌 Pin last output:** after a run, select a step and pin its last output. Debug runs then skip that step and replay
  the pinned output: handy for iterating on a later step without re-running an expensive one. **Unpin** to undo.
- **Mock replies:** select a *AI model* block and enter **Mock replies for debug runs**. Debug runs use those
  replies instead of calling the model.

Pins and mocks only affect runs started from the editor, never published deployments.

## Cost and budgets

Each step shows tokens and latency; the run panel shows totals. Set a [budget](canvas.md#budgets) to stop runs
that exceed tokens, cost or time.

## Tracing

With `LANGSMITH_TRACING=true` and a `LANGSMITH_API_KEY` on the server, each run gets an **Open trace in LangSmith**
link. With `OTEL_EXPORTER_OTLP_ENDPOINT` set, the server also exports OpenTelemetry traces. Secret values are
masked as `***`; list fields in the workflow's `trace_redact` setting to keep their values out of traces entirely.

## Save a run as a test

After a run, **Save as test case** in the Run tab records the input and the expected status, route and output.
The **Tests** tab runs all saved cases (**▶ Run tests**) against the draft or any version. **Record as test**
turns a recorded run (including a production run) into a deterministic regression test that replays the model
and tool answers instead of calling them. See [Publish and integrate](publish.md#tests-before-you-ship).
