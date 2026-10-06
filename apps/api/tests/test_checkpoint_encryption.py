"""Checkpoints encrypted at rest when LANGGRAPH_AES_KEY is set (S14)."""

from __future__ import annotations

import sqlite3

from langgraph.checkpoint.base import empty_checkpoint

from agentcanvas.adapters.langchain.persistence import _sqlite_path, open_persistence

SECRET_STATE = "patient-record-7731"


async def write_and_read(url: str, thread: str) -> object:
    async with open_persistence(url) as p:
        cp = empty_checkpoint()
        cp["channel_values"] = {"note": SECRET_STATE}
        cfg = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
        saved = await p.checkpointer.aput(cfg, cp, {"source": "input", "step": -1, "writes": {}}, {})
        got = await p.checkpointer.aget_tuple(saved)
        return got.checkpoint["channel_values"]["note"]


def raw(url: str) -> bytes:
    with sqlite3.connect(_sqlite_path(url)) as db:
        return b"".join(bytes(row[0]) for row in db.execute("SELECT checkpoint FROM checkpoints"))


async def test_checkpoints_are_ciphertext_with_a_key(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path}/ac.db"
    assert await write_and_read(url, "th_plain") == SECRET_STATE
    assert SECRET_STATE.encode() in raw(url)  # without a key: plain JSON

    monkeypatch.setenv("LANGGRAPH_AES_KEY", "k" * 32)
    assert await write_and_read(url, "th_secret") == SECRET_STATE
    assert raw(url).count(SECRET_STATE.encode()) == 1  # only the earlier, unencrypted checkpoint shows it
    async with open_persistence(url) as p:  # and that one is still readable with the key set
        old = await p.checkpointer.aget_tuple({"configurable": {"thread_id": "th_plain", "checkpoint_ns": ""}})
        assert old.checkpoint["channel_values"]["note"] == SECRET_STATE
