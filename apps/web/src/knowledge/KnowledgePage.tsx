/** Knowledge Studio (S24): knowledge bases a workflow searches with the Search knowledge block. Add documents
 * (files, web pages, text), see how they were split, and try searches before wiring them into a workflow. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { ChunkOut, DocumentOut, KnowledgeBaseOut, SearchOut } from "../api/generated";
import { PROJECT_ID, useEmbeddingModels } from "../api/queries";
import { can } from "../api/session";
import { ModelSelect } from "../ui/ModelSelect";
import { QueryStatus } from "../ui/QueryStatus";
import {
  ACCEPT, blockSnippet, checkFiles, chunkingLabel, citation, embeddingLabel, MODES, type Mode, NAME_PATTERN, STRATEGIES,
  suggestName,
} from "./knowledge";
import { confirmDialog } from "../ui/dialog";
import { PageHeader } from "../ui/PageHeader";

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function KnowledgePage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState(""); // as typed; the name sent is suggestName(name)
  const kbName = suggestName(name);
  const kbs = useQuery({
    queryKey: ["knowledge", PROJECT_ID],
    queryFn: () => apiFetch<KnowledgeBaseOut[]>(`/projects/${PROJECT_ID}/knowledge`),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["knowledge"] });
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<KnowledgeBaseOut>(`/projects/${PROJECT_ID}/knowledge`, { method: "POST", json: body }),
    onSuccess: (kb) => { setSelected(kb.id); setName(""); refresh(); },
  });
  const current = kbs.data?.find((k) => k.id === selected) ?? kbs.data?.[0] ?? null;
  const hasSample = kbs.data?.some((k) => k.name === "handbook");
  const builder = can("builder");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (NAME_PATTERN.test(kbName)) create.mutate({ name: kbName });
  };

  return (
    <div className="page">
      <PageHeader title="Knowledge" description={<>Documents your workflows can search. Add files, web pages or text, then use a <strong>Search knowledge</strong> block
        to find the passages that answer a question, with citations.</>} />
      <QueryStatus q={kbs} what="knowledge bases" />
      {builder && (
        <div className="row gap wrap kb-create">
          <form onSubmit={submit} className="row gap">
            <input aria-label="new knowledge base name" placeholder="New knowledge base, e.g. handbook" value={name}
              onChange={(e) => setName(e.target.value)} />
            <button type="submit" disabled={!NAME_PATTERN.test(kbName) || create.isPending}>Create</button>
            {kbName && kbName !== name && <span className="muted small" data-testid="kb-name-hint">Name: {kbName}</span>}
          </form>
          {kbs.isSuccess && !hasSample && ( // not while the list loads: the sample may be there already
            <button type="button" data-testid="load-sample" disabled={create.isPending}
              onClick={() => create.mutate({ sample: "handbook" })}>
              Load the sample handbook
            </button>
          )}
        </div>
      )}
      {create.isError && <p className="error-box" role="alert">{errorText(create.error)}</p>}
      {kbs.data?.length === 0 && (
        <p className="muted" data-testid="knowledge-empty">No knowledge bases yet. Create one, or load the sample to try searching.</p>
      )}
      <div className="memory-layout">
        <nav aria-label="knowledge bases">
          {kbs.data?.map((k) => (
            <button type="button" key={k.id} className={k.id === current?.id ? "active" : ""} onClick={() => setSelected(k.id)}>
              {k.name} <span className="muted small">· {k.documents} docs</span>
              {k.stale && <span className="badge warn" title="settings changed: reindex">stale</span>}
            </button>
          ))}
        </nav>
        {current && <KnowledgeBase key={current.id} kb={current} builder={builder} onChange={refresh} onDeleted={() => { setSelected(null); refresh(); }} />}
      </div>
    </div>
  );
}

function KnowledgeBase({ kb, builder, onChange, onDeleted }: {
  kb: KnowledgeBaseOut; builder: boolean; onChange: () => void; onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const base = `/knowledge/${kb.id}`;
  const size = kb.chunking.size ?? 800;
  const docs = useQuery({ queryKey: ["knowledge", "docs", kb.id, kb.version], queryFn: () => apiFetch<DocumentOut[]>(`${base}/documents`) });
  const [open, setOpen] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  const [adding, setAdding] = useState(kb.documents === 0);
  const done = () => { onChange(); void qc.invalidateQueries({ queryKey: ["knowledge", "docs", kb.id] }); };
  const upload = useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      return apiFetch<DocumentOut[]>(`${base}/documents`, { method: "POST", body: form });
    },
    onSuccess: done,
  });
  const addUrl = useMutation({
    mutationFn: () => apiFetch<DocumentOut>(`${base}/documents/url`, { method: "POST", json: { url } }),
    onSuccess: () => { setUrl(""); done(); },
  });
  const addText = useMutation({
    mutationFn: () => apiFetch<DocumentOut>(`${base}/documents/text`, { method: "POST", json: { title, text } }),
    onSuccess: () => { setTitle(""); setText(""); done(); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`${base}/documents/${id}`, { method: "DELETE" }),
    onSuccess: done,
  });
  const reindex = useMutation({ mutationFn: () => apiFetch(`${base}/reindex`, { method: "POST" }), onSuccess: done });
  const settings = useMutation({
    mutationFn: (patch: { chunking?: Record<string, unknown>; embedding?: { model: string } }) =>
      apiFetch(base, { method: "PATCH", json: patch }),
    onSuccess: done,
  });
  const embedders = useEmbeddingModels();
  const drop = useMutation({ mutationFn: () => apiFetch(base, { method: "DELETE" }), onSuccess: onDeleted });
  const failure = [upload, addUrl, addText, remove, reindex, settings, drop].find((m) => m.isError)?.error;

  const pick = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    const { ok, refused: no } = checkFiles(files);
    setRefused(no);
    if (ok.length) upload.mutate(ok.map((i) => files[i]));
  };

  return (
    <section aria-label={`knowledge base ${kb.name}`} className="kb">
      <div className="row gap wrap">
        <h3 className="kb-title">{kb.name}</h3>
        <span className="muted small" data-testid="kb-counts">{kb.documents} documents · {kb.chunks} chunks</span>
        <span className="spacer" />
        {builder && (
          <button type="button" className="danger"
            onClick={async () => { if (await confirmDialog({ title: `Delete the knowledge base ${kb.name}?`, body: "Its documents and their search index are deleted too.", confirm: "Delete", danger: true })) drop.mutate(); }}>
            Delete
          </button>
        )}
      </div>
      {kb.description && <p className="muted">{kb.description}</p>}
      <p className="small muted">
        Embeddings: {embeddingLabel(kb)} · Chunks: {chunkingLabel(kb)} · In a workflow: <code>{blockSnippet(kb.name)}</code>
      </p>
      {kb.stale && (
        <div className="callout warn" role="status" data-testid="kb-stale">
          The settings changed since the documents were split and embedded: searches match keywords only until you reindex.{" "}
          {builder && <button type="button" onClick={() => reindex.mutate()} disabled={reindex.isPending}>{reindex.isPending ? "Reindexing…" : "Reindex"}</button>}
        </div>
      )}
      {failure != null && <p className="error-box" role="alert">{errorText(failure)}</p>}
      {refused.length > 0 && <p className="error-box" role="alert">Not uploaded: {refused.join("; ")}</p>}

      <SearchTester base={base} />

      {builder && (
        <details className="kb-add" open={adding} onToggle={(e) => setAdding(e.currentTarget.open)}>
          <summary>Add documents</summary>
          <div className="kb-add-grid">
            <label className="kb-drop">
              <span>Upload files (PDF, Word, HTML, Markdown, text, CSV, JSON; 20 MB each)</span>
              <input type="file" multiple accept={ACCEPT} aria-label="upload files" disabled={upload.isPending}
                onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
              {upload.isPending && <span className="muted small">Reading and indexing…</span>}
            </label>
            <form className="column gap" onSubmit={(e) => { e.preventDefault(); if (url) addUrl.mutate(); }}>
              <span>Add a public web page or document</span>
              <div className="row gap">
                <input aria-label="page address" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
                <button type="submit" disabled={!url || addUrl.isPending}>Add page</button>
              </div>
            </form>
            <form className="column gap" onSubmit={(e) => { e.preventDefault(); if (title && text) addText.mutate(); }}>
              <span>Paste text</span>
              <input aria-label="text title" placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
              <textarea aria-label="text" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
              <button type="submit" disabled={!title || !text || addText.isPending}>Add text</button>
            </form>
          </div>
          <div className="row gap wrap small">
            <span>Split documents</span>
            <select aria-label="chunking strategy" value={kb.chunking.strategy}
              onChange={(e) => settings.mutate({ chunking: { ...kb.chunking, strategy: e.target.value } })}>
              {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
            <span>about</span>
            <select aria-label="chunk size" value={size}
              onChange={(e) => settings.mutate({ chunking: { ...kb.chunking, size: Number(e.target.value) } })}>
              {[200, 400, 800, 1200, 2000].map((n) => <option key={n} value={n}>{n}</option>)}
              {![200, 400, 800, 1200, 2000].includes(size) && <option value={size}>{size}</option>}
            </select>
            <span>characters each</span>
          </div>
          <div className="row gap wrap small">
            <span>Embed with</span>
            <ModelSelect label="embedding model" options={embedders.data} loading={embedders.isPending}
              value={String(kb.embedding.model ?? "builtin")} onChange={(model) => settings.mutate({ embedding: { model } })} />
            <Link className="small" to="/settings/models">Set up more models…</Link>
          </div>
        </details>
      )}

      <QueryStatus q={docs} what="documents" />
      <table className="memory-table">
        <thead><tr><th>Document</th><th>From</th><th>Chunks</th><th /></tr></thead>
        <tbody>
          {docs.data?.map((d) => (
            <DocumentRow key={d.id} doc={d} base={base} open={open === d.id} builder={builder}
              onToggle={() => setOpen(open === d.id ? null : d.id)} onRemove={async () => {
                if (await confirmDialog({ title: `Remove ${d.name}?`, body: "Its passages are taken out of the search index.", confirm: "Remove", danger: true })) remove.mutate(d.id);
              }} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** A web page's host, or null when the stored address is not an http(s) URL (`new URL` threw: the error page). */
function hostOf(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.hostname : null;
  } catch {
    return null;
  }
}

function DocumentRow({ doc, base, open, builder, onToggle, onRemove }: {
  doc: DocumentOut; base: string; open: boolean; builder: boolean; onToggle: () => void; onRemove: () => void;
}) {
  const chunks = useQuery({
    queryKey: ["knowledge", "chunks", doc.id],
    queryFn: () => apiFetch<ChunkOut[]>(`${base}/documents/${doc.id}/chunks`),
    enabled: open,
  });
  const host = doc.url ? hostOf(doc.url) : null;
  return (
    <>
      <tr data-testid="kb-document">
        <td><button type="button" className="link" aria-expanded={open} onClick={onToggle}>{doc.name}</button></td>
        <td className="muted small">
          {host ? <a href={doc.url!} target="_blank" rel="noreferrer">{host}</a> : doc.url || doc.source}
        </td>
        <td>{doc.chunks}</td>
        <td>{builder && <button type="button" aria-label={`remove ${doc.name}`} onClick={onRemove}>Remove</button>}</td>
      </tr>
      {open && (
        <tr><td colSpan={4}>
          <QueryStatus q={chunks} what="chunks" />
          <ol className="kb-chunks" data-testid="kb-chunks">
            {chunks.data?.map((c) => (
              <li key={c.id}>
                {(c.heading || c.page) && <span className="muted small">{[c.heading, c.page ? `page ${c.page}` : null].filter(Boolean).join(" · ")}</span>}
                <pre>{c.text}</pre>
              </li>
            ))}
          </ol>
        </td></tr>
      )}
    </>
  );
}

function SearchTester({ base }: { base: string }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("hybrid");
  const search = useMutation({
    mutationFn: () => apiFetch<SearchOut>(`${base}/search`, { method: "POST", json: { query, mode, top_k: 5 } }),
  });
  return (
    <div className="kb-search">
      <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); if (query.trim()) search.mutate(); }}>
        <input aria-label="search question" placeholder="Try a question, e.g. how many vacation days do I get?" value={query}
          onChange={(e) => setQuery(e.target.value)} className="grow" />
        <div className="segmented" role="radiogroup" aria-label="match by">
          {MODES.map((m) => (
            <button type="button" key={m.id} role="radio" aria-checked={mode === m.id} title={m.help}
              className={mode === m.id ? "active" : ""} onClick={() => setMode(m.id)}>{m.label}</button>
          ))}
        </div>
        <button type="submit" disabled={!query.trim() || search.isPending}>Search</button>
      </form>
      {search.isError && <p className="error-box" role="alert">{errorText(search.error)}</p>}
      {search.data && search.data.results.length === 0 && <p className="muted">Nothing matched.</p>}
      <ol className="kb-results" data-testid="kb-results">
        {search.data?.results.map((r) => (
          <li key={r.chunk_id} data-testid="kb-result">
            <div className="row gap"><strong>{citation(r)}</strong><span className="spacer" /><span className="muted small">score {r.score}</span></div>
            <p>{r.text.length > 400 ? `${r.text.slice(0, 400)}…` : r.text}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
