"""The model bridge under load: many agents calling it at once through one shared HTTP client, as the SDKs do.

Every call must come back as a complete JSON answer. (An agent that got an empty body took it for a streamed answer
and failed with "'tuple' object has no attribute 'choices'": an experiment of several examples on the OpenAI Agents
SDK failed on one of them about one run in three.)
"""

from __future__ import annotations

import asyncio

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agentcanvas.adapters.langchain.bridge import ModelBridge


class _Echo(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "echo-test"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"echo: {messages[-1].content}"))])


async def test_many_simultaneous_calls_all_get_a_complete_answer():
    bridge = ModelBridge()
    await bridge.start()
    failures: list[str] = []
    try:
        with bridge.serving(_Echo()) as key:
            async with httpx.AsyncClient(base_url=bridge.root, limits=httpx.Limits(max_connections=20)) as client:

                async def one(i: int) -> None:
                    try:
                        r = await client.post(
                            "/v1/chat/completions",
                            headers={"authorization": f"Bearer {key}"},
                            json={"model": "x", "messages": [{"role": "user", "content": f"call {i}"}]},
                        )
                        got = r.json()["choices"][0]["message"]["content"]
                        if got != f"echo: call {i}":
                            failures.append(f"call {i}: wrong answer {got!r}")
                    except Exception as exc:  # noqa: BLE001 - every kind of failure is the finding
                        failures.append(f"call {i}: {type(exc).__name__}: {exc}")

                for _ in range(10):  # rounds, so connections are reused between them
                    await asyncio.gather(*(one(i) for i in range(60)))
    finally:
        bridge.close_sockets()
    assert not failures, f"{len(failures)} of 600 calls failed, e.g. {failures[:3]}"


async def test_callers_that_arrive_while_the_bridge_is_starting_get_its_real_address():
    """Two steps starting at once on a fresh event loop: the second used to get the bridge before it listened, and its
    client called http://127.0.0.1:0 (the 'tuple has no attribute choices' failure above, one run in three)."""
    from agentcanvas.adapters.langchain import bridge as bridge_module

    loop = asyncio.get_running_loop()
    bridge_module._BRIDGES.pop(loop, None)
    try:

        async def arrive() -> tuple[object, str]:
            bridge = await bridge_module.model_bridge()
            return bridge, bridge.root  # the address as the caller sees it, the moment it has the bridge

        arrivals = await asyncio.gather(*(arrive() for _ in range(8)))
        assert len({id(b) for b, _ in arrivals}) == 1
        assert all(not root.endswith(":0") for _, root in arrivals), [root for _, root in arrivals]
    finally:
        bridge = bridge_module._BRIDGES.pop(loop, None)
        if bridge is not None:
            bridge.close_sockets()
