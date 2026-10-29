"""G-5 a: file fields, uploads and the references runs carry."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from agentcanvas.db.models import StoredFile
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.contract import breaking_changes, contract
from agentcanvas.ir.files import file_refs, file_schema, is_file_schema, type_matches
from agentcanvas.ir.models import Workflow
from agentcanvas.services.files import FileStore, safe_name, sniff

P = "/v1/projects/prj_default"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40
PDF = b"%PDF-1.7\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"


def docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def photo_flow(wf_id: str, photo: dict | None = None) -> dict:
    return {
        "id": wf_id, "name": "Looks",
        "state": {"channels": [{"name": "who"}, {"name": "photo", "type": photo or file_schema("image/*")},
                               {"name": "out"}]},
        "nodes": [{"id": "n_g", "type": "core.set", "name": "greet",
                   "config": {"values": {"out": "hi {{ state.who }}"}}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_g"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_g"}, "target": {"node": "END"}},
        ],
    }  # fmt: skip


async def make_photo_flow(api) -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": "Looks"})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": photo_flow(wf["id"])}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def upload(api, name: str, data: bytes, content_type: str = "application/octet-stream", project=P):
    return await api.post(f"{project}/uploads", files={"file": (name, data, content_type)})


def finished(text: str) -> dict:
    evs = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    return next(e for e in evs if e["type"] in ("RUN_FINISHED", "RUN_ERROR"))


def test_file_fields_are_json_schema_and_old_documents_stay_valid():
    assert file_schema() == {"type": "object", "format": "file"}
    assert is_file_schema(file_schema("image/*"))
    assert is_file_schema({"type": "array", "items": file_schema()})
    assert not is_file_schema({"type": "object"}) and not is_file_schema(None)
    assert type_matches("image/png", "image/*") and type_matches("application/pdf", "image/*, application/pdf")
    assert not type_matches("application/pdf", "image/*")
    ref = {"kind": "file", "id": "fil_1"}
    assert file_refs(ref) == [ref] and file_refs([ref, "x"]) == [ref] and file_refs("x") == []
    Workflow.model_validate(photo_flow("wf_x"))  # a file field is an ordinary channel


def test_changing_a_field_to_a_file_is_a_breaking_change():
    old = Workflow.model_validate(photo_flow("wf_x", {"type": "object"}))
    new = Workflow.model_validate(photo_flow("wf_x"))
    assert contract(new).input["photo"]["format"] == "file"
    [change] = [c for c in breaking_changes(contract(old), contract(new)) if c.where == "input"]
    assert change.severity == "breaking" and "object → file" in change.message


def test_a_file_is_what_its_content_says_it_is():
    assert sniff("a.png", PNG) == "image/png"
    assert sniff("a.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 20) == "image/jpeg"
    assert sniff("a.gif", b"GIF89a" + b"\x00" * 10) == "image/gif"
    assert sniff("a.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff("a.pdf", PDF) == "application/pdf"
    assert sniff("a.docx", docx()).endswith("wordprocessingml.document")
    assert sniff("notes.md", b"# hi") == "text/markdown" and sniff("x.csv", b"a,b") == "text/csv"
    assert sniff("photo.png", b"<svg onload=alert(1)>") == "text/plain"  # a name does not make it an image
    assert sniff("page.html", b"<script>alert(1)</script>") == "text/plain"
    for name, data in (("a.exe", b"MZ\x90\x00\x00"), ("a.zip", b"PK\x03\x04junk"), ("a.bin", b"\xff\xfe\x00\x01")):
        with pytest.raises(InvalidInput):
            sniff(name, data)


def test_a_file_name_keeps_no_folder():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\Users\\me\\photo.png") == "photo.png"
    assert safe_name(".env") == "env" and safe_name("") == "file" and safe_name(None) == "file"
    assert safe_name("a\x00b\u202egnp.exe") == "abgnp.exe"  # no NUL, no right-to-left override
    long = safe_name("x" * 500 + ".pdf")
    assert len(long) == 200 and long.endswith(".pdf")


async def test_upload_reads_the_type_from_the_content_and_serves_a_download(api_client):
    r = await upload(api_client, "../holiday.png", PNG, "text/html")
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["kind"] == "file" and ref["id"].startswith("fil_") and ref["name"] == "holiday.png"
    assert ref["media_type"] == "image/png" and ref["size"] == len(PNG)

    got = await api_client.get(f"{P}/uploads/{ref['id']}")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"] == "attachment; filename*=UTF-8''holiday.png"
    assert got.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert got.headers["x-content-type-options"] == "nosniff"

    fake = await upload(api_client, "x.png", b"<html><script>alert(1)</script></html>", "image/png")
    assert fake.status_code == 201 and fake.json()["media_type"] == "text/plain"
    assert (await upload(api_client, "a.exe", b"MZ\x90\x00\x03\x00")).status_code == 422
    assert (await upload(api_client, "empty.txt", b"")).status_code == 422
    assert (await api_client.get(f"{P}/uploads/fil_nope")).status_code == 404
    assert (await upload(api_client, "a.png", PNG, project="/v1/projects/prj_nope")).status_code == 404


async def test_uploads_have_a_size_limit(app, api_client):
    app.state.settings.max_upload_mb = 0.01  # about 10 KB
    r = await upload(api_client, "big.txt", b"a" * 20_000)
    assert r.status_code == 422 and "larger than 0.01 MB" in r.json()["detail"]
    assert (await upload(api_client, "small.txt", b"a" * 500)).status_code == 201


async def test_a_run_carries_the_reference_as_the_server_knows_it(app, api_client):
    wf = await make_photo_flow(api_client)
    ref = (await upload(api_client, "me.png", PNG)).json()
    lie = {**ref, "name": "x.pdf", "media_type": "application/pdf", "size": 1}
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada", "photo": lie}})
    out = finished(r.text)
    assert out["type"] == "RUN_FINISHED", out
    assert out["output"]["photo"] == ref  # the server's name, type and size, not the caller's

    bad = await api_client.post(f"/v1/workflows/{wf}/runs/stream",
                                json={"input": {"photo": {"kind": "file", "id": "fil_other"}}})  # fmt: skip
    assert bad.status_code == 422 and "not in this project" in bad.json()["detail"]
    pdf = (await upload(api_client, "a.pdf", PDF)).json()
    wrong = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"photo": pdf}})
    assert wrong.status_code == 422 and "'photo' takes image/*" in wrong.json()["detail"]
    link = {"kind": "file", "url": "http://169.254.169.254/"}
    url = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"photo": link}})
    assert url.status_code == 422 and "exported projects only" in url.json()["detail"]
    text = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"photo": "a picture"}})
    assert text.status_code == 422 and "takes a file" in text.json()["detail"]


async def test_bytes_sent_inline_are_stored_and_the_run_keeps_a_reference(app, api_client):
    wf = await make_photo_flow(api_client)
    inline = {"kind": "file", "name": "inline.png", "data": base64.b64encode(PNG).decode()}
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Bo", "photo": inline}})
    out = finished(r.text)["output"]["photo"]
    assert out["id"].startswith("fil_") and out["media_type"] == "image/png" and "data" not in out
    thread = r.headers["x-thread-id"]
    state = (await api_client.get(f"/v1/threads/{thread}")).json()["values"]
    assert "data" not in state["photo"] and base64.b64encode(PNG).decode() not in json.dumps(state)
    bad = await api_client.post(f"/v1/workflows/{wf}/runs/stream",
                                json={"input": {"photo": {"kind": "file", "data": "not base64!"}}})  # fmt: skip
    assert bad.status_code == 422 and "base64" in bad.json()["detail"]


async def test_runs_read_only_their_own_projects_files(app, api_client):
    ref = (await upload(api_client, "me.png", PNG)).json()
    read = FileStore(app.state.db).for_project("prj_default")
    assert await read(ref) == (PNG, "image/png", "me.png")
    with pytest.raises(ValueError, match="no longer kept"):
        await FileStore(app.state.db).for_project("prj_other")(ref)


async def test_files_go_with_conversations_retention(app, api_client):
    ref = (await upload(api_client, "old.png", PNG)).json()
    fresh = (await upload(api_client, "new.png", PNG)).json()
    async with app.state.db.sessionmaker() as s:
        old = datetime.now(UTC) - timedelta(days=91)
        await s.execute(update(StoredFile).where(StoredFile.id == ref["id"]).values(last_used_at=old))
        await s.commit()
    await app.state.retention.tick(datetime.now(UTC))
    assert (await api_client.get(f"{P}/uploads/{ref['id']}")).status_code == 404
    assert (await api_client.get(f"{P}/uploads/{fresh['id']}")).status_code == 200
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(StoredFile)) == 1


async def test_a_project_keeps_a_limited_amount_of_files(app, api_client):
    app.state.settings.max_project_files_mb = 0.01  # about 10 KB
    assert (await upload(api_client, "a.txt", b"a" * 6000)).status_code == 201
    r = await upload(api_client, "b.txt", b"b" * 6000)
    assert r.status_code == 422 and "keeps at most" in r.json()["detail"]
