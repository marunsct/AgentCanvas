"""G-5 c: Ask AI and AI agent steps show file fields to the model; the warning for a model that cannot see."""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from agentcanvas import filecontent
from agentcanvas.adapters.langchain import nodes as ac
from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.testing import ToolFakeModel
from agentcanvas.ir.files import file_schema
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
SEES = "anthropic:claude-fable-5-1"  # the catalog says vision
BLIND = "mistralai:mistral-large-latest"  # the catalog says no vision


def make_pdf(text: str) -> bytes:
    """A one-page PDF whose text a reader can extract."""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(f'BT /F1 18 Tf 20 100 Td ({text}) Tj ET')} >>\nstream\n"
        f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def make_docx(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class Recorder(ToolFakeModel):
    """A scripted model that keeps the messages it was shown."""

    seen: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any):
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def recorder() -> Recorder:
    return Recorder(messages=iter([]), disable_streaming=True)


def ref(name: str, media: str, data: bytes, fid: str = "fil_1") -> dict:
    return {"kind": "file", "id": fid, "name": name, "media_type": media, "size": len(data)}


def reader(files: dict[str, tuple[bytes, str, str]]):
    async def read(r: dict) -> tuple[bytes, str, str]:
        return files[r["id"]]

    return read


# ------------------------------------------------------------------ blocks ----
async def test_an_image_goes_to_a_model_that_sees_and_is_left_out_for_one_that_does_not():
    r = ref("me.png", "image/png", PNG)
    read = reader({"fil_1": (PNG, "image/png", "me.png")})
    [block] = await filecontent.blocks_for(r, read, SEES)
    assert block == {"type": "image", "base64": base64.b64encode(PNG).decode(), "mime_type": "image/png"}
    [note] = await filecontent.blocks_for(r, read, BLIND)
    assert note["type"] == "text" and "cannot see images" in note["text"] and "base64" not in note
    [unknown] = await filecontent.blocks_for(r, read, "ollama:llava-new")  # not in the catalog: tried with the image
    assert unknown["type"] == "image"


async def test_a_pdf_is_a_file_block_for_a_model_that_sees_and_text_for_one_that_does_not():
    pdf = make_pdf("Invoice total 42 EUR")
    read = reader({"fil_1": (pdf, "application/pdf", "inv.pdf")})
    r = ref("inv.pdf", "application/pdf", pdf)
    [block] = await filecontent.blocks_for(r, read, SEES)
    assert (block["type"], block["mime_type"]) == ("file", "application/pdf")
    assert block["extras"] == {"filename": "inv.pdf"}
    [text] = await filecontent.blocks_for(r, read, BLIND)
    assert text["type"] == "text" and "Invoice total 42 EUR" in text["text"] and "text extracted" in text["text"]


async def test_documents_and_text_files_are_always_text():
    doc, md = make_docx("Quarterly plan"), b"# Notes\nbuy milk"
    read = reader({"a": (doc, filecontent.DOCX, "plan.docx"), "b": (md, "text/markdown", "notes.md")})
    for model in (SEES, BLIND):
        [d] = await filecontent.blocks_for({"id": "a"}, read, model)
        [m] = await filecontent.blocks_for({"id": "b"}, read, model)
        assert "Quarterly plan" in d["text"] and "buy milk" in m["text"]
    long = reader({"a": (b"x" * (filecontent.MAX_TEXT_CHARS + 5), "text/plain", "big.txt")})
    [cut] = await filecontent.blocks_for({"id": "a"}, long, SEES)
    assert "only the first 100,000 characters" in cut["text"]
    broken = reader({"a": (b"%PDF-1.7 not really", "application/pdf", "bad.pdf")})
    [note] = await filecontent.blocks_for({"id": "a"}, broken, BLIND)
    assert "could not be read" in note["text"]


async def test_inline_references_need_no_server():
    data = base64.b64encode(PNG).decode()
    assert await filecontent.read_inline({"data": data, "media_type": "image/png", "name": "a.png"}) == (
        PNG, "image/png", "a.png",
    )  # fmt: skip
    with pytest.raises(ValueError, match="no id on this server"):
        await filecontent.read_inline({"name": "a.png"})
    with pytest.raises(ValueError, match="not base64"):
        await filecontent.read_inline({"data": "!!", "name": "a.png"})


# ------------------------------------------------------------------- nodes ----
async def test_ask_ai_shows_the_files_with_the_prompt():
    model = ac.tag_model(recorder(), SEES)
    node = ac.llm_node(
        model=model, prompt="What is this? {{ state.question }}", output="out", files=["photo"],
        read_file=reader({"fil_1": (PNG, "image/png", "me.png")}),
    )  # fmt: skip
    await node({"question": "Describe it.", "photo": ref("me.png", "image/png", PNG)})
    [[sent]] = model.seen
    assert isinstance(sent, HumanMessage)
    assert sent.content[0] == {"type": "text", "text": "What is this? Describe it."}
    assert sent.content[1]["type"] == "image"

    plain = ac.tag_model(recorder(), SEES)
    await ac.llm_node(model=plain, prompt="Hi", output="out", files=["photo"])({"photo": None})
    assert plain.seen[0][0].content == "Hi"  # no file in the field: an ordinary text message


def wf_with_files(model: str, *, step: str = "core.llm", files: list[str] | None = None) -> Workflow:
    config: dict[str, Any] = (
        {"prompt": "Describe {{ state.question }}", "output": "answer"}
        if step == "core.llm"
        else {"system_prompt": "Be brief."}
    )
    if files is not None:
        config["files"] = files
    channels: list[dict[str, Any]] = [
        {"name": "question"}, {"name": "photo", "type": file_schema("image/*")},
        {"name": "contract", "type": file_schema()}, {"name": "answer"},
    ]  # fmt: skip
    if step == "core.agent":
        channels.append({"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"})
    return Workflow.model_validate({
        "id": "wf_files", "name": "Looks",
        "state": {"channels": channels},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "vis", "config": {"model": model}}],
        "nodes": [{"id": "n_a", "type": step, "name": "look", "config": config}],
        "edges": [
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_a", "port": "model"}},
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_a"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "END"}},
        ],
    })  # fmt: skip


def resources(model: Recorder) -> Resources:
    res = Resources(overrides={"vis": model})
    pdf = make_pdf("Clause 7")
    res.files = reader({"fil_1": (PNG, "image/png", "me.png"), "fil_2": (pdf, "application/pdf", "c.pdf")})
    return res


FILES = {
    "photo": ref("me.png", "image/png", PNG),
    "contract": ref("c.pdf", "application/pdf", b"%PDF", "fil_2"),
}


@pytest.mark.parametrize("compiled", [False, True])
async def test_the_canvas_and_the_export_show_every_file_field_to_ask_ai(compiled, tmp_path):
    wf = wf_with_files(SEES)
    model = recorder()
    if compiled:
        from test_run import load_generated

        graph = load_generated(generate(wf), tmp_path).build_graph(resources(model), checkpointer=InMemorySaver())
    else:
        graph = build_graph(wf, resources(model), checkpointer=InMemorySaver())
    out = await graph.ainvoke({"question": "this", **FILES}, {"configurable": {"thread_id": "t"}})
    [[sent]] = model.seen
    assert [b["type"] for b in sent.content] == ["text", "image", "file"]
    assert out["photo"] == FILES["photo"] and "base64" not in str(out)  # the state keeps references


async def test_a_step_can_limit_which_files_the_model_sees():
    model = recorder()
    graph = build_graph(wf_with_files(SEES, files=["contract"]), resources(model), checkpointer=InMemorySaver())
    await graph.ainvoke({"question": "x", **FILES}, {"configurable": {"thread_id": "t"}})
    assert [b["type"] for b in model.seen[0][0].content] == ["text", "file"]


async def test_a_model_that_cannot_see_gets_the_pdf_text_and_a_note_about_the_image():
    model = recorder()
    graph = build_graph(wf_with_files(BLIND), resources(model), checkpointer=InMemorySaver())
    await graph.ainvoke({"question": "x", **FILES}, {"configurable": {"thread_id": "t"}})
    texts = [b["text"] for b in model.seen[0][0].content]
    assert any("cannot see images" in t for t in texts) and any("Clause 7" in t for t in texts)
    assert all(b["type"] == "text" for b in model.seen[0][0].content)


async def test_an_ai_agent_sees_the_files_without_writing_them_to_the_conversation():
    wf = wf_with_files(SEES, step="core.agent")
    model = recorder()
    graph = build_graph(wf, resources(model), checkpointer=InMemorySaver())
    asked = {"messages": [{"role": "user", "content": "What is on the photo?"}], **FILES}
    out = await graph.ainvoke(asked, {"configurable": {"thread_id": "t"}})
    sent = [m for m in model.seen[0] if m.type == "human"][-1]
    assert [b["type"] for b in sent.content] == ["text", "image", "file"]
    human = next(m for m in out["messages"] if m.type == "human")
    assert human.content == "What is on the photo?"  # the stored message has no file bytes
    assert "base64" not in str(out["messages"])


# --------------------------------------------------------------- validation ----
def codes(wf: Workflow) -> list[tuple[str, str]]:
    return [(d.code, d.message) for d in validate(wf) if d.code == "W025"]


def test_a_file_field_for_a_model_without_vision_is_a_warning_that_names_the_fallback():
    [(code, message)] = codes(wf_with_files(BLIND))
    assert code == "W025" and "photo, contract" in message and "PDFs are read as plain text" in message
    assert codes(wf_with_files(SEES)) == []
    assert codes(wf_with_files("ollama:made-up-model")) == []  # unknown capability: not warned
    assert codes(wf_with_files(BLIND, step="core.agent"))  # agents too
    only = codes(wf_with_files(BLIND, files=["question_only"]))
    assert only == []  # the step is limited to a field that is no file
    plain = wf_with_files(BLIND)
    plain.state.channels = [c for c in plain.state.channels if c.name not in ("photo", "contract")]
    assert codes(plain) == []
