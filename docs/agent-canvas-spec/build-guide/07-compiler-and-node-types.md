# 07 — Compiler & Node Types: the "add a node type" recipe

Every widget in the catalog (spec doc04) is added with the same 9 steps. The tutorial exercise (03 Part 10) walks through them once for `core.counter`; this page is the checklist to reuse for every M3 ticket.

## The 9-step recipe

| # | Step | File(s) | Done when |
|---|------|---------|-----------|
| 1 | **Design** the node: inputs/outputs (ports), config fields, what it reads/writes in state, how it maps to LangChain/LangGraph (doc04 "Compiles to" column) | ticket description | reviewed by a teammate |
| 2 | **IR**: add the type string to `Node.type` (or `Resource.type`) | `ir/models.py` | model validates an example |
| 3 | **Manifest**: ports, `config_schema` (JSON Schema with titles, descriptions, defaults, `format` hints) | `registry/nodes/<type>.json` | `GET /v1/node-types` lists it |
| 4 | **Builder** in the node library: pure function returning a LangGraph node (or agent/subgraph) | `adapters/langchain/nodes.py` | unit test calls it directly |
| 5 | **Interpreter** branch | `adapters/langchain/interpreter.py` | graph builds |
| 6 | **Template** branch (same builder call; optional keys via `.get()`; literals via `py` filter) | `templates/graph.py.j2` | generated code passes ruff + pyright |
| 7 | **Validator** rules specific to the node (required wiring, channel types, safety) | `ir/rules/*.py` | failing/passing IR tests |
| 8 | **Tests**: builder unit test, interpreted run, **generated-equals-interpreted** test, validator tests | `tests/…` | all green |
| 9 | **UI**: icon/category, custom inspector widget if needed, docs link | `web/src/…` | node appears in palette and is configurable |

## Rules that keep generated code honest

1. **One builder, two callers.** Interpreter and template must call the *same* builder with the *same* arguments. Never duplicate logic in the template.
2. **Determinism.** Iterate IR lists in stored order; no timestamps/UUIDs in output; run `ruff format`. `test_generation_is_deterministic` must stay green.
3. **Source map comment** before every block: `# ── @canvas-node <id> "<label>" (<type>) ──`.
4. **No secrets** in generated code — only `{"$secret": "NAME"}` references resolved by `Resources` at runtime.
5. **Readable names.** Generated identifiers come from IR `name` (snake_case, validated); router functions are `<name>_route`.
6. **Side effects after approvals.** If a node both calls external systems and needs approval, split it into two nodes (validator E070; FAQ P-06).

## Mapping cheat-sheet (verified APIs)

| Widget | Builder uses |
|--------|--------------|
| LLM call | `model.ainvoke([HumanMessage(render_template(prompt, state))])` |
| Agent | `create_agent(model, tools=…, system_prompt=…, middleware=[HumanInTheLoopMiddleware(interrupt_on={...}), …], response_format=…, name=…)` |
| Deep Agent | `create_deep_agent(model=…, tools=…, system_prompt=…, subagents=[{"name","description","system_prompt","tools"}], backend=CompositeBackend(default=StateBackend(), routes={"/memories/": StoreBackend(namespace=lambda rt: (...))}), interrupt_on={...}, name=…)` |
| Router | `builder.add_conditional_edges(name, router_fn(cases, default), {route: target})` |
| Map | conditional edge returning `[Send(target, {"item": x}) for x in state[items]]`; results via an `append` channel |
| Join | `builder.add_edge(["a", "b"], "join")` |
| Approval / input | `interrupt({...})` → resume `Command(resume=value)` |
| Retry / timeout / cache | `add_node(..., retry_policy=RetryPolicy(max_attempts=n), timeout=TimeoutPolicy(run_timeout=s), cache_policy=CachePolicy(ttl=s))` + `compile(cache=InMemoryCache())` |
| MCP tools | `async with MCPAdapter({"mcpServers": {name: {"url": url, "auth": token}}}) as a: tools = await a.list_tools()` |
| Guardrails | `PIIMiddleware`, `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`, `ModelRetryMiddleware`, `ModelFallbackMiddleware`, `SummarizationMiddleware` (signatures in 04 M3-T12) |
| Error route | `add_node(..., error_handler=<fn(state, error: langgraph.errors.NodeError) -> Command>)` |

## Code-generation pipeline (as implemented in the reference)

```
validate(wf) ──errors?──► CompileError
      │ ok
prepare context (plain edges, router mappings, model_of, tools_of)
      │
Jinja render (StrictUndefined, `py` filter)
      │
ruff format (stdin) ──non-zero?──► CompileError("generated code is not valid Python")
      │
source text  → M6-T2 packager adds pyproject, README, Dockerfile, tests
```
Later (M6-T2) add `pyright` on the generated project in CI and a smoke test that imports `build_graph()`.

## Equivalence test template (copy for every node type)

```python
@pytest.mark.parametrize("case", CASES)            # inputs covering each branch of the node
async def test_generated_equals_interpreted(tmp_path, case):
    wf = load_ir("examples/<node>.ir.json")
    mod = load_generated(generate(wf), tmp_path)    # helper from tests/test_run.py
    a = await run_case(build_graph(wf, fakes(case), checkpointer=InMemorySaver()), case)
    b = await run_case(mod.build_graph(fakes(case), checkpointer=InMemorySaver()), case)
    assert a == b
```
