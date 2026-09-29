# 08 — Testing & Quality

## 1. Test pyramid

| Level | Tool | What | Speed | Example in reference |
|-------|------|------|-------|----------------------|
| Unit | pytest / Vitest | IR models, validator rules, expressions, builders, reducers (`reduce`), converters (`irToFlow`) | ms | `tests/test_validate.py`, `src/runStore.test.ts` |
| Graph | pytest + fakes | interpreted runs, interrupts/resume, generated-equals-interpreted | < 1 s | `tests/test_run.py` |
| API | pytest + `httpx.ASGITransport` | endpoints incl. SSE parsing | < 1 s | `tests/test_api.py` |
| Integration | pytest + real Postgres | migrations, checkpoints across restarts | seconds | M1-T1, M1-T6 |
| E2E | Playwright + fake backend | user journeys in a real browser | seconds–minutes | `web/e2e.mjs` |
| Contract / conformance | pytest | every node type × adapter (later frameworks) | seconds | doc12 §C.8 |
| Manual with real models | checklist | templates behave sensibly with real LLMs | minutes | M6-T7 |

Target: every PR adds tests at the lowest level that can catch the bug.

## 2. Fake models — read this before writing agent tests

Verified pitfalls (FAQ P-01…P-03):
1. LangChain's fake chat models lack `bind_tools()` → use `agentcanvas.testing.ToolFakeModel`.
2. Token streaming (`stream_mode="messages"`) + a tool-call-only fake reply → `No generations found in stream` → `scripted()` sets `disable_streaming=True`.
3. Scripted fakes remember their position → give **each thread its own** `Resources` (see `resources_factory` in `tests/test_api.py`).

```python
from agentcanvas.testing import scripted, tool_call
smart = scripted(tool_call("send_reply", {"text": "Hi"}), "Reply sent.")   # 1st call: tool call, 2nd: final text
res = Resources(overrides={"smart_model": smart, "fast_model": scripted("low")})
```
To assert what the model **received** (e.g. PII redaction), subclass `ToolFakeModel` and record `messages` in `_generate`.

## 3. Async tests

- `pytest-asyncio` with `asyncio_mode = "auto"` (in `pyproject.toml`): write `async def test_…` directly.
- Async fixtures that need cleanup use `yield` (see the `client` fixture).
- For the app lifespan in tests: `async with api.lifespan(api.app): …` then `httpx.ASGITransport(app=api.app)`.

## 4. Database tests

- Use a dedicated database (`TEST_DATABASE_URL`) created per CI job; run `alembic upgrade head` in a session-scoped fixture.
- Isolate tests with transactions rolled back at the end, or unique thread/workflow ids per test.
- LangGraph checkpoint tables are created by `saver.setup()`; call it in the fixture.

## 5. Property-based tests (M1-T4)

```python
from hypothesis import given, strategies as st
@given(st.lists(command_strategy(), max_size=20))
def test_commands_are_invertible(cmds):
    wf0 = example_workflow(); wf = wf0
    inverses = []
    for c in cmds:
        inverses.append(c.invert(wf)); wf = c.apply(wf)
    for inv in reversed(inverses):
        wf = inv.apply(wf)
    assert wf == wf0
```

## 6. E2E (Playwright)

- Start `uv run uvicorn agentcanvas.dev.fake_server:app --port 8000` and `pnpm dev` (Playwright `webServer` config can start both).
- Prefer role/text selectors (`getByRole("button", { name: "Approve" })`), never CSS classes.
- Each test creates its own workflow/thread ids.

## 7. Static quality gates (CI blocks merges)

| Gate | Command |
|------|---------|
| Python lint & format | `uv run ruff check . && uv run ruff format --check .` |
| Python types | `uv run pyright` (standard mode, 0 errors) |
| Import boundaries (M3+) | `uv run lint-imports` |
| TS types | `pnpm exec tsc -p tsconfig.json` |
| Tests | `uv run pytest -q`, `pnpm test`, `pnpm exec playwright test` |
| Generated code | tests run `ruff` via `generate()` and pyright on a generated sample |
| Dependencies | `pip-audit` / `pnpm audit --prod` (M8) |

## 8. Performance tests
- Validator: 200-node IR validates < 150 ms (`pytest-benchmark`).
- Canvas: 500 nodes pan/zoom at 60 fps (Playwright + `performance.now()` sampling) — M2 stretch goal.
- Runner: k6 script with 200 concurrent SSE runs using the fake backend (M8-T5).

## 9. Code review checklist
- [ ] Does the change keep interpreter and generated code equivalent?
- [ ] New config keys: optional ones read with `.get()` in templates?
- [ ] Any `eval`, string-built SQL, secrets in logs? (must be no)
- [ ] Side effects placed after approvals?
- [ ] Errors surface to the UI as diagnostics or `RUN_ERROR`, not silent?
- [ ] Tests at the right level; names describe behaviour.
