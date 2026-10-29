"""Fine-tuning export (G-10): good conversations as JSONL training examples.

Two sources, one file format at the end:

- **Accepted runs.** A run is accepted when people (annotation reviews, thumbs from API callers and share links) or,
  if asked, online evaluators scored it at least `min_score` on average and nobody gave it a thumbs-down. Its
  example is the run's turn as the model saw it: the input as the user message, then the tool calls the final model
  step asked for, their results, and its answer, taken from the events the run kept.
- **A dataset version.** Each example's input is the user message and its reference answer the assistant's.

Formats: `openai` is OpenAI's chat fine-tuning format (`{"messages": [...], "tools": [...]}`, tool calls as
`tool_calls` with JSON-string arguments); `messages` is a plain one (`{"messages": [...], "metadata": {...}}`, tool
calls with their arguments as objects, scores and ids in `metadata`) for other trainers.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Run, RunScore, Thread
from agentcanvas.ir.models import Workflow
from agentcanvas.services.run_search import strings

Format = Literal["openai", "messages"]
HUMAN = ("annotation", "feedback")
MAX_EXAMPLES = 5000


class Skipped(BaseModel):
    ref: str
    reason: str


class Exported(BaseModel):
    examples: list[dict[str, Any]]
    skipped: list[Skipped] = Field(default_factory=list)


def user_text(input: Any) -> str:
    """The user's message in a run's input: the last user message of `messages`, else the input's strings."""
    messages = input.get("messages") if isinstance(input, dict) else None
    if isinstance(messages, str):
        return messages.strip()
    if isinstance(messages, list):
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role", m.get("type")) in ("user", "human"):
                return " ".join(strings(m.get("content"))).strip()
    if isinstance(input, dict):
        rest = {k: v for k, v in input.items() if k != "messages"}
        return "\n".join(strings(rest)).strip()
    return " ".join(strings(input)).strip()


def system_prompt(wf: Workflow | None) -> str:
    """The first system prompt written on the workflow's agents and AI steps."""
    for n in wf.nodes if wf else []:
        text = n.config.get("system_prompt") or n.config.get("prompt")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def accepted(scores: list[RunScore], min_score: float, include_online: bool) -> tuple[bool, str]:
    """Whether a run's scores accept it, and why not."""
    counted = [
        s for s in scores if s.score is not None and (s.source in HUMAN or (include_online and s.source == "online"))
    ]
    if not counted:
        return False, "nobody has scored it"
    if any(s.source == "feedback" and (s.score or 0) < 0.5 for s in counted):
        return False, "it got a thumbs-down"
    mean = sum(s.score or 0 for s in counted) / len(counted)
    if mean < min_score:
        return False, f"its scores average {mean:.2f}, under {min_score:g}"
    return True, ""


def turns(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The final model step's turns as normalised messages: assistant messages (with `tool_calls` as {name, arguments})
    and `tool` messages with the results, in order. Replies of helper agents are left out."""
    replies = [e for e in events if e.get("type") == "ac.model_reply"]
    if not replies:
        return []
    last = replies[-1].get("value") or {}
    node, own = last.get("canvas_node_id"), last.get("agent")  # the agent's own name: others are its helpers
    replies = [e for e in replies if (e.get("value") or {}).get("agent") == own]
    out: list[dict[str, Any]] = []
    tool_events = [e for e in events if e.get("type") == "ac.tool_io" and (e.get("value") or {}).get("agent") == own]
    used: set[int] = set()
    for e in replies:
        value = e.get("value") or {}
        if value.get("canvas_node_id") != node:
            continue
        reply = value.get("reply")
        calls = reply.get("tool_calls") if isinstance(reply, dict) else None
        text = reply.get("content") if isinstance(reply, dict) else reply
        if calls:
            out.append({"role": "assistant", "content": text or "", "tool_calls": [
                {"name": c.get("name"), "arguments": c.get("args") or {}} for c in calls]})  # fmt: skip
            for c in calls:
                for i, t in enumerate(tool_events):
                    tv = t.get("value") or {}
                    if i not in used and tv.get("tool") == c.get("name"):
                        used.add(i)
                        body = tv.get("output") if "error" not in tv else f"error: {tv['error']}"
                        content = body if isinstance(body, str) else json.dumps(body, default=str)
                        out.append({"role": "tool", "name": c.get("name"), "content": content})
                        break
        else:
            out.append({"role": "assistant", "content": str(text or "")})
    return out


def _schema_of(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int | float):
        return {"type": "number"}
    if isinstance(value, list):
        return {"type": "array"}
    if isinstance(value, dict):
        return {"type": "object"}
    return {"type": "string"}


def render(messages: list[dict[str, Any]], fmt: Format, metadata: dict[str, Any]) -> dict[str, Any]:
    """One training example in the chosen format."""
    if fmt == "messages":
        return {"messages": messages, "metadata": metadata}
    out_messages: list[dict[str, Any]] = []
    tools: dict[str, dict[str, Any]] = {}
    pending: list[str] = []  # ids of the tool calls the next tool messages answer
    for n, m in enumerate(messages):
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for k, c in enumerate(m["tool_calls"]):
                cid = f"call_{n}_{k}"
                pending.append(cid)
                arguments = json.dumps(c["arguments"], default=str)
                calls.append({"id": cid, "type": "function", "function": {"name": c["name"], "arguments": arguments}})
                params = tools.setdefault(c["name"], {})
                for key, value in (c["arguments"] or {}).items():
                    params.setdefault(key, _schema_of(value))
            out_messages.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif m["role"] == "tool":
            out_messages.append({"role": "tool", "tool_call_id": pending.pop(0) if pending else "call_0",
                                 "content": m["content"]})  # fmt: skip
        else:
            out_messages.append({"role": m["role"], "content": m["content"]})
    out: dict[str, Any] = {"messages": out_messages}
    if tools:  # inferred from the calls seen: the fine-tuning API wants the functions listed
        out["tools"] = [
            {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": props}}}
            for name, props in tools.items()
        ]
    return out


class FinetuneService:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def from_runs(
        self,
        wf: Workflow | None,
        wf_id: str,
        *,
        run_ids: list[str] | None,
        fmt: Format,
        min_score: float,
        include_online: bool,
        system: str | None,
        limit: int,
    ) -> Exported:
        q = (
            select(Run)
            .join(Thread, Thread.id == Run.thread_id)
            .where(Thread.workflow_id == wf_id, Run.status == "success")
            .order_by(Run.started_at.desc())
        )
        if run_ids is not None:
            q = q.where(Run.id.in_(run_ids))
        runs = list((await self.s.scalars(q)).all())
        scores: dict[str, list[RunScore]] = {}
        if runs:
            found = await self.s.scalars(select(RunScore).where(RunScore.run_id.in_([r.id for r in runs])))
            for sc in found:
                scores.setdefault(sc.run_id, []).append(sc)
        sys_msg = system if system is not None else system_prompt(wf)
        out = Exported(examples=[])
        for run in runs:
            if len(out.examples) >= limit:
                break
            ok, why = accepted(scores.get(run.id, []), min_score, include_online)
            if not ok:
                if run_ids is not None:  # asked for by name: say why it is not in
                    out.skipped.append(Skipped(ref=run.id, reason=why))
                continue
            asked = user_text((run.recording or {}).get("input"))
            replies = turns(run.events or [])
            if not asked or not replies or replies[-1]["role"] != "assistant":
                out.skipped.append(Skipped(ref=run.id, reason="no recorded input and answer to learn from"))
                continue
            messages = [*([{"role": "system", "content": sys_msg}] if sys_msg else []),
                        {"role": "user", "content": asked}, *replies]  # fmt: skip
            human = [s for s in scores[run.id] if s.source in HUMAN]
            meta = {"run_id": run.id, "thread_id": run.thread_id, "workflow_id": wf_id,
                    "scores": {s.evaluator: s.score for s in scores[run.id]}, "reviewed_by": len(human)}  # fmt: skip
            out.examples.append(render(messages, fmt, meta))
        if run_ids is not None:
            known = {r.id for r in runs}
            out.skipped += [
                Skipped(ref=r, reason="not a successful run of this workflow") for r in run_ids if r not in known
            ]
        return out

    @staticmethod
    def from_examples(
        examples: list[dict[str, Any]], ds_id: str, fmt: Format, system: str | None, wf: Workflow | None, limit: int
    ) -> Exported:
        sys_msg = system if system is not None else system_prompt(wf)
        out = Exported(examples=[])
        for ex in examples:
            if len(out.examples) >= limit:
                break
            asked, answer = user_text(ex.get("input")), " ".join(strings(ex.get("reference"))).strip()
            if not asked or not answer:
                out.skipped.append(
                    Skipped(ref=str(ex.get("id")), reason="the example has no input text or no reference answer")
                )
                continue
            messages = [*([{"role": "system", "content": sys_msg}] if sys_msg else []),
                        {"role": "user", "content": asked}, {"role": "assistant", "content": answer}]  # fmt: skip
            out.examples.append(
                render(messages, fmt, {"dataset_id": ds_id, "example_id": ex.get("id"), "split": ex.get("split")})
            )
        return out


def to_jsonl(examples: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in examples)
