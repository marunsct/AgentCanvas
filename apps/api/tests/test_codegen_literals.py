"""Every Python code generator writes the texts of a workflow so that they read back unchanged (X-2, X-5).

A corpus of awkward texts (quotes, backslashes, CRLF, leading indentation, unicode, braces) goes through every block
setting that ends up as a string literal, in every generator (flows, and agents designed for another framework);
`ast.parse` of the generated module must find each text as one of its string constants, exactly as it was typed.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

import pytest

from agentcanvas import designs
from agentcanvas.adapters.agno import codegen as agno
from agentcanvas.adapters.crewai import codegen as crewai
from agentcanvas.adapters.google_adk import codegen as google_adk
from agentcanvas.adapters.langchain import codegen as langgraph
from agentcanvas.adapters.llamaindex import codegen as llamaindex
from agentcanvas.adapters.microsoft_agent_framework import codegen as maf
from agentcanvas.adapters.openai_agents import codegen as openai_agents
from agentcanvas.adapters.pydantic_ai import codegen as pydantic_ai
from agentcanvas.adapters.strands import codegen as strands
from agentcanvas.ir.models import Workflow

CORPUS = [
    "one line",
    "first line\n  indented second\n\tand a tab",
    "    leading indentation\nkept as typed",
    'say """hi"""\nthen go',
    'ends with a quote\n"',
    "C:\\new\\table\nsecond line \\",
    "windows\r\nline breaks\r\n",
    "a lone\rreturn",
    "unicode: café, 日本語, \U0001f600\nzero\u200bwidth, line\u2028separator",
    "braces {x} }{ {{ state.question }}\n{% if state.question %}yes{% endif %}",
    "\nstarts with a line break",
    "control \x0c\x1b[0m characters\n",
]

GENERATORS: dict[str, Callable[[Workflow], str]] = {
    "openai_agents": openai_agents.generate,
    "crewai": crewai.generate,
    "google_adk": google_adk.generate,
    "pydantic_ai": pydantic_ai.generate,
    "agno": agno.generate,
    "strands": strands.generate,
    "llamaindex": llamaindex.generate,
    "microsoft_agent_framework": maf.generate,
    "langgraph": lambda wf: langgraph.generate(wf, format_code=False),
}


def _node(kind: str, text: str) -> dict[str, Any]:
    if kind == "set":
        return {"type": "core.set", "config": {"values": {"answer": text}}}
    if kind == "llm":
        return {"type": "core.llm", "config": {"prompt": text, "output": "answer"}}
    if kind == "agent":
        return {"type": "core.agent", "config": {"system_prompt": text}}
    if kind == "human_input":
        return {"type": "core.human_input", "config": {"prompt": text, "output": "answer"}}
    if kind == "search":
        return {"type": "knowledge.search", "config": {"knowledge_base": "docs", "query": text, "output": "answer"}}
    if kind == "task":
        return {"type": "crewai.task", "config": {"role": "Writer", "goal": text, "backstory": text,
                                                  "description": text, "expected_output": text}}  # fmt: skip
    raise AssertionError(kind)


KINDS = ["set", "llm", "agent", "human_input", "search"]
CASES = [(framework, kind) for framework in GENERATORS for kind in KINDS] + [("crewai", "task")]


def _workflow(kind: str, text: str) -> Workflow:
    node = {"id": "n_step", "name": "step", **_node(kind, text)}
    edges: list[dict[str, Any]] = [
        {"id": "e_in", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_step"}},
        {"id": "e_out", "kind": "flow", "source": {"node": "n_step"}, "target": {"node": "END"}},
    ]
    if kind in ("llm", "agent", "task"):
        edges.append({"id": "w_model", "kind": "wiring", "source": {"node": "r_model"},
                      "target": {"node": "n_step", "port": "model"}})  # fmt: skip
    channels = [{"name": "answer", "default": ""}, {"name": "question", "default": ""},
                {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]  # fmt: skip
    return Workflow.model_validate(
        {
            "id": "wf_literals", "name": "Literals", "state": {"channels": channels},
            "resources": [{"id": "r_model", "type": "model.chat", "name": "model",
                           "config": {"model": "openai:gpt-5.5"}}],
            "nodes": [node],
            "edges": edges,
        }
    )  # fmt: skip


def _strings(source: str) -> list[str]:
    return [n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


@pytest.mark.parametrize(("framework", "kind"), CASES)
def test_texts_read_back_unchanged(framework: str, kind: str) -> None:
    for text in CORPUS:
        source = GENERATORS[framework](_workflow(kind, text))
        assert text in _strings(source), f"{framework} changed {text!r} in a {kind} block"


@pytest.mark.parametrize("framework", list(designs.AGENT_FRAMEWORKS))
def test_designed_agents_keep_their_texts(framework: str) -> None:
    structured = designs.AGENT_FRAMEWORKS[framework].structured_output
    for text in CORPUS:
        schema = {
            "type": "object",
            "description": text,
            "required": ["answer"],
            "properties": {
                "answer": {"type": "string", "description": text},
                "note": {"type": "string", "description": text},
            },
        }
        design = designs.AgentDesign(name="helper", workflow="Literals", instructions=text,
                                     model=designs.ModelChoice("model", "openai", "gpt-5.5"),
                                     output_schema=schema if structured else None)  # fmt: skip
        found = _strings(designs.generate(framework, design)).count(text)
        assert found == (4 if structured else 1), f"{framework} changed {text!r}"
