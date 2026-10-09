"""S16: run a real agent from each supported framework as a canvas step, against a local fake model server.

S21: also design an Agent block for the framework and run it in a LangGraph flow (a plain answer, a typed answer and
a tool that needs approval, with a scripted canvas model served through the model bridge), then export it as a
native project and run that project's own tests.

CI's `frameworks` job runs this once per framework, with that framework installed at the version AgentCanvas pins
(interop.FRAMEWORKS): `uv run --with <package> python scripts/smoke_frameworks.py <framework>`. The fake server
speaks the OpenAI chat completions and Anthropic messages APIs and answers "pong: <last user text>", so no keys or
network are needed. `agentspec` instead checks exported flows with pyagentspec, the Agent Spec reference library.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
QUESTION = "hello from the canvas"

# The agent each framework runs, as a workspace file (tools/smoke.py). BASE is the fake server's /v1 address.
AGENTS = {
    "openai_agents": """
from agents import Agent, OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI
set_tracing_disabled(True)
agent = Agent(name="smoke", instructions="Reply briefly.",
              model=OpenAIChatCompletionsModel(model="fake", openai_client=AsyncOpenAI(base_url=BASE, api_key="k")))
""",
    "pydantic_ai": """
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
agent = Agent(OpenAIChatModel("fake", provider=OpenAIProvider(base_url=BASE, api_key="k")), instructions="Reply.")
""",
    "agno": """
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
agent = Agent(model=OpenAILike(id="fake", base_url=BASE, api_key="k"), instructions="Reply briefly.")
""",
    "strands": """
from strands import Agent
from strands.models.openai import OpenAIModel
def agent():  # a factory: a fresh agent (and conversation) per run
    return Agent(model=OpenAIModel(client_args={"base_url": BASE, "api_key": "k"}, model_id="fake"),
                 system_prompt="Reply briefly.", callback_handler=None)
""",
    "llamaindex": """
from workflows import Workflow, step
from workflows.events import StartEvent, StopEvent
class Echo(Workflow):
    @step
    async def answer(self, ev: StartEvent) -> StopEvent:
        return StopEvent(result=f"pong: {ev.input}")
agent = Echo(timeout=30)
""",
    "crewai": """
from crewai import LLM, Agent, Crew, Task
llm = LLM(model="openai/fake", base_url=BASE, api_key="k")
helper = Agent(role="Helper", goal="Answer", backstory="Brief.", llm=llm, verbose=False)
agent = Crew(agents=[helper], tasks=[Task(description="Answer: {input}", expected_output="An answer", agent=helper)],
             verbose=False)
""",
    "google_adk": """
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
agent = LlmAgent(name="smoke", model=LiteLlm(model="openai/fake", api_base=BASE, api_key="k"),
                 instruction="Reply briefly.")
""",
    "microsoft_agent_framework": """
from agent_framework.openai import OpenAIChatCompletionClient
agent = OpenAIChatCompletionClient("fake", base_url=BASE, api_key="k").as_agent(name="smoke", instructions="Reply.")
""",
    "claude_agent_sdk": """
from claude_agent_sdk import ClaudeAgentOptions
agent = ClaudeAgentOptions(system_prompt="Reply briefly.", max_turns=1, model="claude-fake",
                           env={"ANTHROPIC_BASE_URL": ROOT, "ANTHROPIC_API_KEY": "k"})
""",
}


# ------------------------------------------------------------------ fake model server ----
def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        texts = [p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text"]
        if any(texts):
            return texts[-1]
    return ""


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # quiet
        pass

    def _json(self, body: Any, status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, events: list[tuple[str | None, Any]]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, data in events:
            chunk = (
                f"event: {name}\n" if name else ""
            ) + f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
            self.wfile.write(chunk.encode())
        self.wfile.flush()

    def do_GET(self) -> None:
        self._json({"object": "list", "data": [{"id": "fake", "object": "model", "owned_by": "smoke"}]})

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        text = "pong: " + _last_user_text(body.get("messages") or [])
        if self.path.endswith("/chat/completions"):
            return self._openai(body, text)
        if self.path.split("?")[0].endswith("/messages"):
            return self._anthropic(body, text)
        if self.path.split("?")[0].endswith("/count_tokens"):
            return self._json({"input_tokens": 10})
        self._json({"error": {"message": f"not faked: {self.path}"}}, 404)

    def _openai(self, body: dict[str, Any], text: str) -> None:
        usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        base = {"id": "chatcmpl-1", "created": int(time.time()), "model": body.get("model", "fake")}
        if not body.get("stream"):
            msg = {"role": "assistant", "content": text}
            choice = {"index": 0, "message": msg, "finish_reason": "stop"}
            return self._json({**base, "object": "chat.completion", "choices": [choice], "usage": usage})
        chunk = {**base, "object": "chat.completion.chunk"}
        events: list[tuple[str | None, Any]] = [
            (None, {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]}),
            (None, {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        ]
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append((None, {**chunk, "choices": [], "usage": usage}))
        self._sse([*events, (None, "[DONE]")])

    def _anthropic(self, body: dict[str, Any], text: str) -> None:
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model", "claude-fake"),
               "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3}}  # fmt: skip
        if not body.get("stream"):
            return self._json({**msg, "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})
        self._sse([
            ("message_start", {"type": "message_start", "message": {**msg, "content": [], "stop_reason": None}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"}),
        ])  # fmt: skip


def serve() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


# ------------------------------------------------------------------ checks ----
async def run_framework(framework: str) -> str:
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from agentcanvas.adapters.langchain.interop import framework_agent_node

    root = serve()
    with tempfile.TemporaryDirectory() as ws:
        (Path(ws) / "tools").mkdir()
        (Path(ws) / "tools" / "__init__.py").write_text("")
        header = f"BASE = {root + '/v1'!r}\nROOT = {root!r}\n"
        (Path(ws) / "tools" / "smoke.py").write_text(header + AGENTS[framework])

        class S(TypedDict, total=False):
            question: str
            reply: str

        node = framework_agent_node(
            node_id="n_smoke", name="smoke", framework=framework, workspace=Path(ws),
            import_path="tools.smoke:agent", prompt="{{ state.question }}", output="reply",
        )  # fmt: skip
        g = StateGraph(S)
        g.add_node("smoke", node)
        g.add_edge(START, "smoke")
        g.add_edge("smoke", END)
        out = await g.compile().ainvoke({"question": QUESTION})
    reply = str(out.get("reply", ""))
    if QUESTION not in reply:
        raise SystemExit(f"{framework}: expected the fake model's answer to '{QUESTION}', got {reply!r}")
    return reply


def check_agentspec() -> None:
    from pyagentspec.flows.flow import Flow
    from pyagentspec.serialization import AgentSpecDeserializer, AgentSpecSerializer

    from agentcanvas.agentspec import export_flow, import_flow
    from agentcanvas.ir.models import Workflow
    from agentcanvas.services.workflows import TEMPLATES

    for path in sorted(TEMPLATES.glob("*.ir.json")):
        wf = Workflow.model_validate(json.loads(path.read_text()))
        doc, _ = export_flow(wf)
        flow = AgentSpecDeserializer().from_json(json.dumps(doc))
        assert isinstance(flow, Flow), path.name
        again = json.loads(AgentSpecSerializer().to_json(flow))
        ir, _, _ = import_flow(again, wf_id="wf_check")  # pyagentspec keeps our metadata
        assert ir["nodes"] == doc["metadata"]["agentcanvas"]["ir"]["nodes"], path.name
        print(f"agentspec: {path.name} OK ({len(flow.nodes)} nodes)")
    sample = (HERE.parent / "tests" / "agentspec_sample.json").read_text()
    flow = AgentSpecDeserializer().from_json(sample)
    ir, _, _ = import_flow(json.loads(AgentSpecSerializer().to_json(flow)), wf_id="wf_check")
    Workflow.model_validate(ir)
    print("agentspec: the pyagentspec sample imports")


# ------------------------------------------------------------------ S21: designed agents ----
async def lookup_order(order_id: str) -> str:
    """Look up an order by its id."""
    return f"order {order_id} ships tomorrow"


def designed_workflow(framework: str, mode: str) -> Any:
    from agentcanvas.ir.models import Workflow

    cfg: dict[str, Any] = {"system_prompt": "You help with orders."}
    channels = [{"name": "messages", "reducer": "add_messages"}]
    if mode == "typed":
        cfg["response_format"] = {
            "type": "object",
            "required": ["answer"],
            "properties": {"answer": {"type": "string"}},
        }
        channels.append({"name": "structured_response"})
    if mode == "approval":
        cfg["approve_tools"] = ["lookup_order"]
    model = "anthropic:claude-sonnet-5-5" if framework == "claude_agent_sdk" else "openai:gpt-5.5"
    return Workflow.model_validate({
        "id": "wf_smoke", "name": "Orders", "state": {"channels": channels},
        "resources": [
            {"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": model, "temperature": 0.1}},
            {"id": "r_t", "type": "tool.python", "name": "lookup_order",
             "config": {"import_path": "tools.orders:lookup_order"}},
        ],
        "nodes": [{"id": "n_a", "type": "core.agent", "name": "helper", "engine": framework, "config": cfg}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_a"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_a", "port": "model"}},
            {"id": "w2", "kind": "wiring", "source": {"node": "r_t"}, "target": {"node": "n_a", "port": "tools"}},
        ],
    })  # fmt: skip


async def run_designed(framework: str, mode: str) -> str:
    """A drawn agent in a LangGraph flow; the scripted model calls the tool, then answers."""
    from agentcanvas.adapters import create_adapter
    from agentcanvas.adapters.langchain.nodes import Resources
    from agentcanvas.ids import new_id
    from agentcanvas.runtime.types import ResumeSpec, RunSpec

    wf = designed_workflow(framework, mode)
    final = json.dumps({"answer": "It ships tomorrow"}) if mode == "typed" else "It ships tomorrow"
    replies: list[Any] = [{"content": "", "tool_calls": [{"name": "lookup_order", "args": {"order_id": "A1"}}]}, final]
    if mode == "typed" and framework in ("strands", "llamaindex"):  # these ask for the typed answer through a tool
        replies.append({"content": "", "tool_calls": [{"name": "Answer", "args": {"answer": "It ships tomorrow"}}]})
    mocks = {"model": replies}
    adapter = create_adapter("langchain")
    async with adapter.persistence(None):
        resources = Resources(overrides={"lookup_order": lookup_order})
        thread = new_id("th")
        question = {"messages": [{"role": "user", "content": "Where is order A1?"}]}
        spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), input=question, mocks=mocks)
        pauses = 0
        while True:
            events = [e async for e in adapter.execute(spec, resources)]
            last = events[-1]
            if last.get("type") == "RUN_ERROR":
                raise SystemExit(f"{framework} {mode}: {last.get('message')}")
            if last.get("outcome") != "interrupt" or pauses > 2:
                break
            pauses += 1
            spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"),
                           resume=ResumeSpec(value={"approved": True}), mocks=mocks)  # fmt: skip
    out = last.get("output") or {}
    reply = str((out.get("messages") or [{}])[-1].get("content"))
    tools_ran = [e for e in events if e.get("name") == "ac.tool_io"]
    if "ships tomorrow" not in reply or (mode == "approval") != (pauses == 1) or not tools_ran:
        raise SystemExit(f"{framework} {mode}: unexpected run (reply {reply!r}, {pauses} pauses, tools {tools_ran})")
    if mode == "typed" and out.get("structured_response") != {"answer": "It ships tomorrow"}:
        raise SystemExit(f"{framework} typed: structured_response is {out.get('structured_response')!r}")
    return reply


def check_export(framework: str) -> None:
    """The drawn agent exported as a native project: its own tests pass with the framework installed."""
    import subprocess

    from agentcanvas.designs import design_for
    from agentcanvas.designs.export import export_files

    for mode in ("plain", "typed"):
        wf = designed_workflow(framework, mode)
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as out:
            (Path(ws) / "tools").mkdir()
            (Path(ws) / "tools" / "orders.py").write_text(
                'async def lookup_order(order_id: str) -> str:\n    """Look up an order."""\n    return order_id\n'
            )
            files = export_files(wf, design_for(wf, wf.nodes[0]), framework, Path(ws))
            for rel, content in files.items():
                path = Path(out) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content if isinstance(content, bytes) else content.encode())
            root = Path(out) / next(iter(files)).split("/")[0]
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
            r = subprocess.run(cmd, cwd=root, check=False, capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"{framework} export ({mode}): its tests failed\n{r.stdout[-4000:]}{r.stderr[-2000:]}")
            print(f"{framework}: export ({mode}) tests pass")


def main() -> None:
    which = sys.argv[1:] or [*AGENTS, "agentspec"]
    for name in which:
        if name == "agentspec":
            check_agentspec()
            continue
        if name not in AGENTS:
            raise SystemExit(f"unknown framework '{name}' (choose from {', '.join(AGENTS)} or agentspec)")
        os.environ.setdefault("OPENAI_API_KEY", "k")  # some frameworks check for a key before using the client
        print(f"{name}: {asyncio.run(run_framework(name))!r}")
        for mode in ("plain", "typed", "approval"):
            print(f"{name}: designed agent ({mode}): {asyncio.run(run_designed(name, mode))!r}")
        check_export(name)


if __name__ == "__main__":
    main()
