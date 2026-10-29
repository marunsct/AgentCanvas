"""Prefixed, time-sortable identifiers (ADR 0009): wf_01J9…, n_01J9…, th_…, run_…"""

from __future__ import annotations

from ulid import ULID

PREFIXES = {
    "org",
    "usr",
    "prj",
    "wf",
    "ver",
    "dep",
    "ds",
    "dsv",
    "ex",
    "evl",
    "exp",
    "exr",
    "gate",
    "th",
    "run",
    "n",
    "r",
    "e",
    "sec",
    "con",
    "trg",
    "req",
    "evt",
    "dd",
    "tc",
    "pol",
    "oev",  # online evaluator (S13)
    "rsc",  # run score
    "alr",  # evaluation alert
    "aq",  # annotation queue
    "cpc",  # Copilot conversation (S14)
    "prop",  # Copilot proposal
    "aqi",  # annotation queue item
    "ann",  # a reviewer's annotation of a queue item
    "kb",  # knowledge base (S24)
    "kdoc",  # knowledge document
    "cal",  # cost alert (S26)
    "fil",  # a file a person attached to a run (G-5)
    "tok",  # a person's API token (G-13)
    "sgr",  # a SCIM group (G-13)
    "oac",  # an OAuth connection (G-7)
}


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix '{prefix}'")
    return f"{prefix}_{ULID()}"
