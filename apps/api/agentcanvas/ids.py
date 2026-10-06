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
}


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix '{prefix}'")
    return f"{prefix}_{ULID()}"
