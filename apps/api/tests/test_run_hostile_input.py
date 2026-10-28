"""A run started with input nobody planned for: wrong types, NUL characters, huge or deeply nested values.

Whatever arrives, the run ends in a terminal event the client can read (success, or an error that says why), the
thread is free again, and everything it left behind (the run record, its events, the thread's state, its history)
can be read back. The stored side matters on Postgres, which refuses what SQLite keeps (a NUL character in a JSON
text, for one); CI runs these on both.
"""

from __future__ import annotations

import json

import pytest
from test_frameworks import greeter
from test_runs_v1 import events

NESTED: object = "leaf"
for _ in range(80):
    NESTED = {"a": NESTED}
INPUTS = [
    {"who": None},
    {"who": 0},
    {"who": 10**30},
    {"who": True},
    {"who": []},
    {"who": {"deep": {"er": [1, 2, {"x": None}]}}},
    {"who": NESTED},
    {"who": "A" * 200_000},
    {"who": "nul \x00 inside"},
    {"who": "\x00"},
    {"who": "lone \ud800 surrogate"},
    {"who": "{{ state.greeting }} {{ 7*7 }} ${jndi:ldap://x}"},
    {"who": "emoji 😀 " + chr(0x2028) + " rtl " + chr(0x202E)},
    {"unknown_channel": "x", "who": "ok"},
    {},
]
TERMINAL = {"RUN_FINISHED", "RUN_ERROR"}


@pytest.mark.parametrize("n", range(len(INPUTS)))
async def test_streamed_run_ends_cleanly_and_leaves_readable_records(api_client, n):
    wf = await greeter(api_client)
    thread = f"th_hostile_{n}"
    try:
        body = json.dumps({"input": INPUTS[n], "thread_id": thread}, ensure_ascii=False)
        r = await api_client.post(
            f"/v1/workflows/{wf['id']}/runs/stream", content=body.encode("utf-8", "replace"),
            headers={"content-type": "application/json"},
        )  # fmt: skip
    except UnicodeError:
        pytest.skip("the client cannot send that text")
    assert r.status_code < 500, (r.status_code, r.text[:300])
    if r.status_code >= 400:
        return  # refused up front, with a reason
    evs = events(r.text)
    assert any(e["type"] in TERMINAL for e in evs), evs[-3:]
    runs = (await api_client.get(f"/v1/threads/{thread}/runs")).json()
    assert len(runs) == 1 and runs[0]["status"] in {"success", "error"}, runs
    assert (await api_client.get(f"/v1/threads/{thread}")).json()["status"] in {
        "idle",
        "error",
    }  # error: the last run's
    for path in (f"/v1/runs/{runs[0]['id']}", f"/v1/threads/{thread}/history", f"/v1/runs/{runs[0]['id']}/trace"):
        got = await api_client.get(path)
        assert got.status_code < 500, (path, got.status_code, got.text[:300])
    again = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "second"}, "thread_id": thread}
    )
    assert again.status_code == 200, "the thread must take a next message"
    assert events(again.text)[-1]["type"] == "RUN_FINISHED"
    assert (await api_client.get(f"/v1/threads/{thread}")).json()["status"] == "idle"
