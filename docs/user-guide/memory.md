# Memory

Workflows have two kinds of memory.

- **Short-term:** the state of a thread. Everything in the channels, including chat history, is kept between runs
  on the same thread and checkpointed at every step.
- **Long-term:** facts kept across conversations, stored per organisation in the database (with semantic search
  on Postgres + pgvector). This page is about long-term memory.

## Spaces and sharing

Every memory belongs to a **space** (a name you choose, e.g. `knowledge` or `preferences`) and is shared per:

| Shared per | Meaning |
|---|---|
| `user` (default) | Each end user has their own memories. Needs a context field with source `user` so the run knows who the user is. API callers pass `user_id`; it is stored as `api:<deployment>:<user_id>` so API users never mix with organisation members. |
| `workflow` | One set of memories for everyone using the workflow. |
| `thread` | Memories that last for one conversation. |

Same space + same sharing = same memories, whichever widget reads or writes them.

## Using memory in a workflow

- **Remember** saves a channel's value under a key (a template, e.g. `preference:{{ state.topic }}`).
- **Recall** searches a space with a query template and writes up to *top_k* matches to a channel. An empty query
  returns the most recent memories.
- **Forget** deletes one key.
- **Memory tools** give an agent `search_memory` and `save_memory`, so the agent decides when to use memory.
- **Saved files folder** blocks mount a Planning Agent folder (e.g. `/memories/`) on long-term memory, so files the agent writes
  there survive the conversation. The *Research brief* template does this.

The *RAG Q&A* template answers questions from the `knowledge` space: fill it first with a Remember step or from
the Memory page.

## The Memory page

**Memory** in the top bar lists what has been remembered, per space and owner (for example
`memories/user/usr_dev`). Pick a namespace to see its items, search within it, delete single items, or erase the
whole namespace (for example everything one end user asked you to forget).

## Retention

Long-term memories are kept until deleted, unless the server sets `AGENTCANVAS_MEMORY_RETENTION_DAYS`, in which
case memories not updated for that long are removed by the daily retention job.
