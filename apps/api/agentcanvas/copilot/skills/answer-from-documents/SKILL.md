---
name: answer-from-documents
description: Build a workflow that answers from the project's documents (retrieval, RAG) with citations, and check it finds the right passages. Use when the person mentions documents, a knowledge base, a handbook, FAQs, PDFs or "answer from our docs".
---

# Answering from documents

1. The documents live in a knowledge base (Knowledge page). Ask which one, or tell the person to create one there
   (they upload files, add web pages or paste text). Do not invent its name.
2. Add `knowledge.search`: `knowledge_base` is the base's name, `query` is usually `{{ state.question }}`,
   `mode: hybrid` unless asked otherwise, `top_k` 3 to 5, `output` a field such as `passages`, and `sources_output`
   when the answer should list its sources.
3. Then an AI step (`core.llm`) whose prompt includes the passages and says to answer only from them and to quote
   them by `[cite]`, and to say "I don't know" when they do not cover the question.
4. To check it, propose a dataset example whose reference lists the relevant documents and a `retrieval`
   evaluator (recall at k), so the person can see that search finds the right passages before tuning the prompt.
