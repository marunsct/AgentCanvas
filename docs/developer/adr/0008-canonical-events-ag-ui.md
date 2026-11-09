# 0008 — Canonical run events are AG-UI based

Status: accepted · Date: 2026-09-29

## Context
The UI must not depend on LangGraph's raw stream format so that other frameworks (and LangGraph v3 streaming) can be added later.

## Decision
The runner translates LangGraph v2 stream parts into AG-UI-style events (`RUN_STARTED`, `STEP_STARTED`, `TEXT_MESSAGE_CONTENT`, `STATE_DELTA`, `CUSTOM ac.*`, …) as specified in spec doc 12 §C.3.

## Consequences
+ Framework-neutral UI. − A translator per framework/stream version.
