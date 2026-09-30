# Spike SP-3 — Is LangGraph v3 event streaming ready to replace `astream(version="v2")`?

| | |
|---|---|
| Sprint | S8 (time-boxed, 0.5 d) |
| Question | Can the runner produce the canonical events (doc 12 §C.3) from `astream_events(version="v3")` instead of `astream(..., stream_mode=[...], subgraphs=True)`? |
| Outcome | **Not yet — keep v2** (review item V-04 stands). Re-check on each LangGraph minor release. |

## Findings (langgraph 1.2.12, probed on 2026-09-29)

1. **It is still experimental.** Every call emits `LangChainBetaWarning: The v3 streaming protocol on Pregel is experimental.`
2. **The call shape differs from the docs.**
   - `astream_events(..., version="v3")` is a coroutine: `run = await graph.astream_events(...)` returns an `AsyncGraphRunStream`.
   - `run.output` is a **method**, so you write `await run.output()`.
   - Projections are `values`, `messages`, `lifecycle`, `subgraphs`, `interrupts` and `extensions`.
3. **The default stream carries `values` events only.** Each event is `{"type": "event", "method": "values", "params": {"namespace", "timestamp", "data", "interrupts"}, "seq"}`.
   - It has no step start/finish events.
   - For a flat graph, the `lifecycle` projection yielded nothing.
   - STEP_STARTED/STEP_FINISHED (canvas node status, durations, lineage) would have to be rebuilt from state diffs, which loses which node wrote what.
4. **Interrupts and resume work.** `Command(resume=...)` resumes into a new stream as expected, and interrupts arrive on `values` events.

## Decision

- The canonical event contract stays as it is. The adapter keeps `astream` v2 with `updates`, `messages`, `custom`, `tasks` and `subgraphs`.
- Switching later only touches `adapters/langchain/events.py` and `adapter.execute`; the UI and the public API do not change.
- Revisit when both of these hold:
  - the beta warning is gone
  - node lifecycle events are available for root graphs
