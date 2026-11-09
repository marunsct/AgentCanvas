# Knowledge

A **knowledge base** is a set of documents your workflows can search: policies, manuals, help pages, notes.
Open it from **Knowledge** in the top bar. A workflow searches it with the **Search knowledge** block, which finds
the passages that best answer a question and numbers them so the answer can cite them as [1], [2].

## Try the sample

Click **Load the sample handbook**. It adds `handbook`: 22 pages of the employee handbook of Lumen Labs, a made-up
company. Type a question in the search box, for example *how many vacaton days do I get* (the typo is on
purpose), and switch between the three ways to match:

| Match by | What it does | Good for |
|---|---|---|
| **Hybrid** (default) | Both rankings below, fused (reciprocal-rank fusion) | Most questions |
| **Meaning** | Embeddings: compares what the question and each passage are about | Word forms, misspellings; with a provider model, synonyms too |
| **Keywords** | Exact words, ranked by BM25 | Codes, names, rare terms |

Then create a workflow from the **Ask your documents** template: it searches `handbook` and answers with
citations.

## Add documents

Create a knowledge base (its name is lower-case letters, digits, `-` and `_`, e.g. `product-docs`), then open
**Add documents**:

- **Upload files**: PDF, Word (`.docx`), HTML, Markdown, text, CSV and JSON, and pictures (PNG, JPEG, TIFF, BMP,
  WebP); up to 20 files at a time, 20 MB each. PDF pages are kept, so citations name the page. Pages of a scanned
  PDF and picture files are read with OCR ([Tesseract](https://github.com/tesseract-ocr/tesseract), included in the
  Docker image; elsewhere install it and it is found on the path). A page that already has text is not OCR'd. See
  *Reading scans* below.
- **Add a public web page or document** by its address. Only public addresses are fetched: private, internal
  and cloud-metadata addresses are refused, also after a redirect. Pages up to 5 MB.
- **Add every page of a sitemap**: give the address of a `sitemap.xml` (or a sitemap index) and its pages are added,
  up to 500, those on the sitemap's own host.
- **Paste text** with a title.

The same text twice is refused. Click a document to see how it was split into chunks.

**Rename…** changes the knowledge base's name and description. A Search knowledge block finds the knowledge base
by its name, so a block that names the old one needs the new name.

### Keep web pages up to date

Under the documents, **Fetch web pages again** sets how often (every 6 hours, daily or weekly) the server fetches the
base's web pages and sitemaps again; **Fetch now** does it at once. A page whose text changed replaces its document
(the old passages leave the index), a page new to a followed sitemap is added, and a page that left the sitemap is
removed. A page that cannot be fetched keeps its document and is listed as failed in the result shown next to the
setting. A page you removed by hand is not added back while the sitemap still lists it. The same refusal of private
and internal addresses applies to every fetch, redirects and sitemaps included.

Google Drive and other sources: download the files and upload them, or connect the source's MCP server to an
agent (see [Agents and connectors](agents-and-connectors.md)).

## Chunking and embeddings

Documents are split into **chunks** of about the size you choose (800 characters by default; a project can set
other defaults for its new knowledge bases under **Settings › Knowledge**):

- **By heading** (default): a chunk stays under one Markdown heading and remembers it.
- **By paragraph**, **by sentence**, or **fixed size** windows that overlap.

Each chunk gets an **embedding**. The built-in embedder works offline and needs no key: it matches word forms
and close spellings ("reimbursed" finds "reimbursement"), not synonyms. For meaning across different words, use
a provider's embedding model: pick it under **Add documents › Embed with**, or set the default for new knowledge
bases in **Settings › Models › By purpose**. The list shows only embedding models that can answer: OpenAI's,
Mistral's or Gemini's when their key is a project secret, and the embedding models your local server lists (for
example `text-embedding-nomic-embed-text-v1.5` in LM Studio or `nomic-embed-text` in Ollama; a chat model cannot
embed). An embedding model on the same server as the project's model uses the same address and key.

Changing the chunking or the embedding model marks the knowledge base **stale**; click **Reindex** to split and
embed every document again. Until then it is searched by keywords only, whatever the block's **Match by** says
(the stored vectors may come from the old model, and comparing them with a question embedded by the new one gives
noise), and an export ships it without vectors.

## The Search knowledge block

| Setting | Meaning |
|---|---|
| Knowledge base | Its name, e.g. `handbook` |
| Search for | A template, by default `{{ state.question }}` |
| Match by | Hybrid, meaning or keywords |
| At most | How many passages to keep (5) |
| Minimum score | Drop weaker passages, from 0 to 1 (0 keeps the best ones); the scale below |
| Save the passages in | A list of `{cite, title, text, page, source, score}` |
| Save the sources in | Optional: the citation list to show under an answer |

A passage's `score` and the minimum score are on one scale from 0 to 1, whatever the match:

| Match by | Score |
|---|---|
| Keywords | Relative to the best match, which scores 1 (0.5: half as strong a match) |
| Meaning | How close the passage's embedding is to the question's (cosine similarity) |
| Hybrid | 1: first in both rankings; 0.5: first in one of them; lower further down |

An AI step can then quote them, for example:

```jinja
{% for p in state.passages %}[{{ p.cite }}] {{ p.title }}: {{ p.text }}
{% endfor %}
```

**Exports** ship each knowledge base the workflow searches as `src/knowledge/<name>.jsonl`, and the exported
project searches it the same way the server does.

## Measure retrieval

Add a **retrieval** evaluator on the workflow's Evaluate page. It scores the passages a run found against the
example's `reference.relevant`, the documents a good search finds:

```json
{"input": {"question": "How many vacation days do I get?"}, "reference": {"relevant": ["vacation-policy"]}}
```

Config: `channel` (default `passages`), `k`, `metric` (`recall`, `precision`, `hit` or `mrr`) and `match_by`
(`source`: the file name without extension or the page address; `title`; `id`; or `text`, a phrase the passage
contains).

To compare search settings, run an **experiment** with two targets, one overriding the block's mode. On the
sample handbook's 50 questions (`registry/knowledge/handbook.eval.jsonl`, with typos and paraphrases), hybrid
search finds the right page in the top 3 for 95% of questions against 93% for keywords alone (MRR 0.94 against
0.92).

## Limits

- On PostgreSQL with pgvector (the Docker image) search runs in the database and holds up to 1,000,000 chunks per
  knowledge base: meaning uses an approximate nearest-neighbour index (HNSW, cosine) per embedding size, keywords
  use full-text search weighted like BM25, and hybrid fuses both as before. Scores and citations are the same as
  above. Embedding sizes of 384, 512, 768, 1024, 1536 and 3072 are indexed; another size is searched exactly, which
  is slower on large bases.
- On SQLite (and PostgreSQL without pgvector) search runs in memory on each server: up to 50,000 chunks per
  knowledge base.
- Roles: everyone can search; builders add, change and delete.

## Reading scans

OCR is on when Tesseract is installed (`AGENTCANVAS_OCR=auto`, the default; `off` never runs it). Only PDF pages
with almost no text and picture files are read. Set the language with `AGENTCANVAS_OCR_LANGUAGES` (Tesseract codes
joined by `+`, e.g. `eng+deu`); the Docker image has English, and
`docker build --build-arg OCR_LANGS="deu fra" -f infra/Dockerfile.api .` adds others. At most
`AGENTCANVAS_OCR_MAX_PAGES` (100) pages of a file are read, and the text says when pages were left out. OCR makes
mistakes on poor scans: check what it read under the document's chunks. Without Tesseract a scan is refused with a
message that says so.
