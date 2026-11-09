"""A paused run asks the person through their MCP client (F-1, MCP 2026-07-28 "Multi Round-Trip Requests").

A modern client that declares `elicitation` (form mode) gets `resultType: input_required` when the run pauses: one
`elicitation/create` form for the pause (keyed by its interrupt id), and a `requestState` the client echoes back when
it retries the same `tools/call` with the person's answer in `inputResponses`. The client shows the form to the
person, so an autonomous agent no longer approves its own tool calls through the `answer` tool.

`requestState` is HMAC-signed with the server's secret key and binds the answer to the deployment, the key the
caller used (its fingerprint: a rotated key invalidates it), the conversation, the pause, the tool and a digest of
its arguments, and a short expiry. A pause is answered once: the runtime refuses an interrupt that is no longer
pending.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
TASKS = "io.modelcontextprotocol/tasks"
STATE_TTL_S = 15 * 60
MAX_SHOWN = 1500  # characters of the paused value shown in a form's message
PRIMITIVE = ("string", "number", "integer", "boolean")


def capabilities(params: dict[str, Any]) -> dict[str, Any]:
    meta = params.get("_meta")
    caps = meta.get(META_CAPABILITIES) if isinstance(meta, dict) else None
    return caps if isinstance(caps, dict) else {}


def elicits(params: dict[str, Any]) -> bool:
    """The client takes form-mode elicitation (an empty `elicitation` object means form mode)."""
    e = capabilities(params).get("elicitation")
    return isinstance(e, dict) and (not e or "form" in e)


def tasks(params: dict[str, Any]) -> bool:
    """The client declared the Tasks extension on this request."""
    ext = capabilities(params).get("extensions")
    return isinstance(ext, dict) and TASKS in ext


# ------------------------------------------------------------------------------------------- forms ----
def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _shown(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
    return text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN] + "…"


def _flat(schema: Any) -> bool:
    """A JSON Schema a form can show: an object whose properties are all primitives (or enums of strings)."""
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return False
    return all(isinstance(p, dict) and p.get("type") in PRIMITIVE for p in schema["properties"].values())


def form(interrupt: dict[str, Any], workflow: str) -> dict[str, Any]:
    """The `elicitation/create` request (form mode) that asks a person for this pause."""
    payload = _obj(interrupt.get("payload"))
    kind = interrupt.get("kind")
    decide = {
        "type": "object",
        "properties": {
            "approve": {"type": "boolean", "title": "Approve", "default": False},
            "comment": {"type": "string", "title": "Comment", "description": "optional; the reason when rejecting"},
        },
        "required": ["approve"],
    }
    if kind == "tool_approval":
        calls = [f"- {r.get('name')}({_shown(r.get('args') or r.get('arguments') or {})})"
                 for r in payload.get("action_requests") or [] if isinstance(r, dict)]  # fmt: skip
        message = f"{workflow} wants to make these tool calls:\n" + "\n".join(calls) + "\nAllow them?"
        schema = decide
    elif kind == "approval":
        what = payload.get("question") or payload.get("message") or payload.get("title")
        shown = payload.get("value")
        message = str(what) if what else f"{workflow} asks for your approval to go on."
        if shown not in (None, "", {}, []):
            message += f"\n\n{_shown(shown)}"
        schema = decide
    else:
        message = str(payload.get("prompt") or payload.get("question") or f"{workflow} needs an answer.")
        if payload.get("error"):
            message += f"\n(The last answer was not accepted: {payload['error']})"
        asked = payload.get("response_schema")
        if _flat(asked):
            schema = asked
        else:
            answer = dict(asked) if isinstance(asked, dict) and asked.get("type") in PRIMITIVE else {"type": "string"}
            schema = {"type": "object", "properties": {"answer": {"title": "Answer", **answer}}, "required": ["answer"]}
    return {"method": "elicitation/create", "params": {"mode": "form", "message": message, "requestedSchema": schema}}


Outcome = Literal["resume", "ask_again", "declined", "dismissed"]


def answer(interrupt: dict[str, Any], response: Any, approve_value: Any) -> tuple[Outcome, Any]:
    """What the person's elicitation result means for the pause: resume with a value, ask again (a malformed or
    incomplete answer), or leave the run paused (declined a question, or dismissed the form). `approve_value(approve,
    comment)` shapes an approval's resume value."""
    if not isinstance(response, dict) or response.get("action") not in ("accept", "decline", "cancel"):
        return "ask_again", None
    kind = interrupt.get("kind")
    deciding = kind in ("approval", "tool_approval")
    content = _obj(response.get("content"))
    if response["action"] == "cancel":
        return "dismissed", None
    if response["action"] == "decline":
        return ("resume", approve_value(False, None)) if deciding else ("declined", None)
    if deciding:
        approve, comment = content.get("approve"), content.get("comment")
        if not isinstance(approve, bool):
            return "ask_again", None
        return "resume", approve_value(approve, comment if isinstance(comment, str) and comment.strip() else None)
    payload = _obj(interrupt.get("payload"))
    if _flat(payload.get("response_schema")):
        return "resume", content
    if "answer" not in content:
        return "ask_again", None
    return "resume", content["answer"]


# ----------------------------------------------------------------------------------- request state ----
@dataclass(frozen=True)
class State:
    deployment_id: str
    key: str  # fingerprint of the deployment key the caller used
    thread_id: str
    interrupt_id: str
    tool: str
    arguments: str  # digest of the tool call's arguments
    expires: int


def digest(arguments: Any) -> str:
    raw = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def key_fingerprint(api_key_hash: str) -> str:
    return api_key_hash[:16]


def _mac(secret: str) -> bytes:
    return hashlib.sha256(b"agentcanvas mcp requestState\x00" + secret.encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(secret: str, state: State) -> str:
    body = json.dumps({"v": 1, "d": state.deployment_id, "k": state.key, "t": state.thread_id, "i": state.interrupt_id,
                       "n": state.tool, "a": state.arguments, "x": state.expires}, separators=(",", ":"))  # fmt: skip
    tag = hmac.new(_mac(secret), body.encode(), hashlib.sha256).digest()
    return f"{_b64(body.encode())}.{_b64(tag)}"


def issue(secret: str, *, deployment_id: str, key: str, thread_id: str, interrupt_id: str, tool: str,
          arguments: Any, now: float | None = None) -> str:  # fmt: skip
    expires = int((now if now is not None else time.time()) + STATE_TTL_S)
    return sign(secret, State(deployment_id, key, thread_id, interrupt_id, tool, digest(arguments), expires))


class InvalidState(ValueError):
    pass


def verify(secret: str, token: Any, *, deployment_id: str, key: str, tool: str, arguments: Any,
           now: float | None = None) -> State:  # fmt: skip
    """The state, if `token` is one this server signed for this deployment, key, tool and arguments and it has not
    expired; InvalidState (saying why, without echoing it) otherwise."""
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 4096:
        raise InvalidState("requestState is not one this server issued")
    body_b64, tag_b64 = token.split(".")
    try:
        body, tag = _unb64(body_b64), _unb64(tag_b64)
    except (binascii.Error, ValueError):
        raise InvalidState("requestState is not one this server issued") from None
    if not hmac.compare_digest(hmac.new(_mac(secret), body, hashlib.sha256).digest(), tag):
        raise InvalidState("requestState is not one this server issued")
    data = json.loads(body)
    state = State(data["d"], data["k"], data["t"], data["i"], data["n"], data["a"], int(data["x"]))
    if state.deployment_id != deployment_id or state.key != key:
        raise InvalidState("requestState was issued to another caller")
    if state.tool != tool or state.arguments != digest(arguments):
        raise InvalidState("requestState belongs to another request: retry the same tool call with the same arguments")
    if state.expires < (now if now is not None else time.time()):
        raise InvalidState("requestState has expired")
    return state
