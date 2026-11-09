"""G-5 b: a visitor attaches a file on a share link."""

from __future__ import annotations

import json

from test_files import PDF, PNG
from test_publish import P
from test_share import ADA, BO, events, share_on

from agentcanvas.ir.files import file_schema


def looker(wf_id: str, photo: dict) -> dict:
    shown = (
        "{{ state.photo | length }} files"
        if photo.get("type") == "array"
        else "{{ state.photo.name }} {{ state.photo.size }}"
    )
    return {
        "id": wf_id, "name": "Looker",
        "state": {"channels": [{"name": "question"}, {"name": "photo", "type": photo}, {"name": "out"}]},
        "nodes": [{"id": "n_g", "type": "core.set", "name": "look",
                   "config": {"values": {"out": "{{ state.question }} / " + shown}}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_g"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_g"}, "target": {"node": "END"}},
        ],
    }  # fmt: skip


async def shared_looker(api, photo: dict | None = None) -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": "Looker"})).json()
    ir = looker(wf["id"], photo or file_schema("image/*"))
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    pub = (await api.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()
    return (await share_on(api, pub["deployment"]["id"]))["token"]


async def attach(api, token: str, name: str, data: bytes, visitor: str = ADA):
    return await api.post(f"/v1/share/{token}/files", data={"visitor": visitor}, files={"file": (name, data)})


async def ask(api, token: str, message: str, files: list[str], visitor: str = ADA):
    body = {"message": message, "visitor": visitor, "files": files}
    return await api.post(f"/v1/share/{token}/runs/stream", json=body)


async def test_the_page_says_which_file_input_takes_attachments(api_client):
    token = await shared_looker(api_client)
    info = (await api_client.get(f"/v1/share/{token}")).json()
    assert info["attachments"] == {"field": "photo", "accept": "image/*", "multiple": False}
    assert info["input"]["field"] == "question"


async def test_a_visitor_attaches_an_image_and_the_run_gets_the_reference(api_client):
    token = await shared_looker(api_client)
    up = await attach(api_client, token, "../me.png", PNG)
    assert up.status_code == 201, up.text
    ref = up.json()
    assert ref["name"] == "me.png" and ref["media_type"] == "image/png" and ref["id"].startswith("fil_")

    r = await ask(api_client, token, "who is this?", [ref["id"]])
    done = events(r.text)[-1]
    assert done["type"] == "RUN_FINISHED" and done["answer"] == f"who is this? / me.png {len(PNG)}"

    empty = await ask(api_client, token, "", [ref["id"]])  # a file alone is a message
    assert events(empty.text)[-1]["answer"].startswith("Please look at the attached file. / me.png")
    assert (await ask(api_client, token, "", [])).status_code == 422


async def test_visitors_use_only_their_own_files_and_the_inputs_types(api_client):
    token = await shared_looker(api_client)
    ada = (await attach(api_client, token, "ada.png", PNG)).json()
    assert (await ask(api_client, token, "mine now", [ada["id"]], visitor=BO)).status_code == 404  # not Bo's file
    assert (await ask(api_client, token, "x", ["fil_nope"])).status_code == 404
    assert (await ask(api_client, token, "x", [ada["id"], ada["id"]])).status_code == 422  # one file, not two

    pdf = await attach(api_client, token, "a.pdf", PDF)  # the input takes images only
    assert pdf.status_code == 422 and "image/*" in pdf.json()["detail"]
    fake = await attach(api_client, token, "x.png", b"<script>alert(1)</script>")
    assert fake.status_code == 422  # its content decides, not its name
    assert (await attach(api_client, token, "a.png", PNG, visitor="short")).status_code == 422
    assert (await attach(api_client, "sh_unknown", "a.png", PNG)).status_code == 404

    # the file is not served to anyone through the link: there is no download route for visitors
    assert (await api_client.get(f"/v1/share/{token}/files/{ada['id']}")).status_code in (404, 405)


async def test_a_list_of_files_and_a_workflow_without_files(api_client):
    token = await shared_looker(api_client, {"type": "array", "items": file_schema()})
    assert (await api_client.get(f"/v1/share/{token}")).json()["attachments"]["multiple"] is True
    one = (await attach(api_client, token, "a.pdf", PDF)).json()
    two = (await attach(api_client, token, "b.pdf", PDF)).json()
    r = await ask(api_client, token, "both", [one["id"], two["id"]])
    assert events(r.text)[-1]["answer"] == "both / 2 files"

    from test_share import publish

    _, dep, _ = await publish(api_client)
    plain = (await share_on(api_client, dep))["token"]
    assert (await api_client.get(f"/v1/share/{plain}")).json()["attachments"] is None
    no = await attach(api_client, plain, "a.png", PNG)
    assert no.status_code == 422 and "does not take files" in no.json()["detail"]
    assert json.loads(no.text)["detail"]
