# Memory

Workflows have two kinds of memory.

- **Short-term:** the state of a thread. Everything in the channels, including chat history, is kept between runs
  on the same thread and checkpointed at every step.
- **Long-term:** facts kept across conversations, stored per organisation in the database (with semantic search
  when the server names an embedding model, see below). This page is about long-term memory.

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

## Semantic search

By default **Recall**, `search_memory` and the Memory page's search rank a space's memories by the words they share
with the query: the server reads up to 1,000 memories of the space and ranks them each time, so a memory that uses
other words ("beverage" for "tea") is not found and a space with more than 1,000 memories is only searched in part.

Set `AGENTCANVAS_MEMORY_EMBEDDING` on the server to `builtin` (the offline embedder: word forms and spellings, 512
dimensions) or `<provider>:<model>` (for example `openai:text-embedding-3-small`, with the provider's usual key
variable such as `OPENAI_API_KEY`, or `ollama:nomic-embed-text` with `AGENTCANVAS_MEMORY_EMBEDDING_BASE_URL`) and the
store embeds each memory when it is saved. Recall then asks the store for the best *top_k* by meaning (cosine; on
Postgres through pgvector's HNSW index, on SQLite through sqlite-vec) and reads nothing else. A query that is empty
still returns the most recent memories.

- Memories use **one** embedding model per server, unlike knowledge bases, which choose per base: the vectors live in
  one column of one size. Known models' sizes are looked up; for another one set `AGENTCANVAS_MEMORY_EMBEDDING_DIMS`
  (otherwise it is measured once at startup).
- Memories saved before the setting, or under another model of the same size, are embedded once in the background
  when the server starts (each is saved again as it is, which also restarts its retention clock).
- On Postgres, changing to a model of **another size** is refused while the store holds vectors of the old size:
  memory keeps ranking by words and the log says why. Drop the `store_vectors` and `vector_migrations` tables to start
  the index again; the memories stay and are embedded again at the next start.
- If the provider is unreachable or the package is missing, the server starts and memory ranks by words as before.

## Retention

Long-term memories are kept until deleted, unless the server sets `AGENTCANVAS_MEMORY_RETENTION_DAYS`, in which
case memories not updated for that long are removed by the daily retention job. An organisation's admin may choose
its own number of days under **Settings › Organisation**.
