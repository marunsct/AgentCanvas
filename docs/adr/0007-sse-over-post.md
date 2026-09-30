# 0007 — SSE over POST for run streams

Status: accepted · Date: 2026-09-29

## Context
Runs stream events to the browser; WebSockets add infrastructure the MVP does not need.

## Decision
Run endpoints return `text/event-stream` from a POST; the browser reads it with `fetch` + stream reader. WebSockets only later for collaboration.

## Consequences
+ Works through standard proxies (with buffering off). − No built-in reconnection; re-attach endpoint needed later.
