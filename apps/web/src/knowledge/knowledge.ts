/** Knowledge Studio helpers (S24): names, uploads and how search results read. */
import type { KnowledgeBaseOut, SearchResult } from "../api/generated";

export const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const ACCEPT = ".pdf,.docx,.html,.htm,.md,.markdown,.txt,.csv,.json,.jsonl,.rst,.log,.yaml,.yml";
export const MODES = [
  { id: "hybrid", label: "Hybrid", help: "words and meaning together (best in most cases)" },
  { id: "semantic", label: "Meaning", help: "embeddings: finds word forms and close spellings" },
  { id: "keyword", label: "Keywords", help: "exact words, ranked by BM25" },
] as const;
export type Mode = (typeof MODES)[number]["id"];
export const STRATEGIES = [
  { id: "markdown", label: "By heading" },
  { id: "paragraphs", label: "By paragraph" },
  { id: "sentences", label: "By sentence" },
  { id: "fixed", label: "Fixed size" },
] as const;

/** A name the server accepts, from what someone typed ("HR Handbook" -> "hr-handbook"). */
export function suggestName(text: string): string {
  return text.toLowerCase().trim().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

/** Files to upload, and the ones refused before uploading (too big or an unknown type). */
export function checkFiles(files: { name: string; size: number }[]): { ok: number[]; refused: string[] } {
  const known = ACCEPT.split(",");
  const ok: number[] = [];
  const refused: string[] = [];
  files.forEach((f, i) => {
    const ext = f.name.includes(".") ? f.name.slice(f.name.lastIndexOf(".")).toLowerCase() : "";
    if (!known.includes(ext)) refused.push(`${f.name}: not a supported file type`);
    else if (f.size > MAX_FILE_BYTES) refused.push(`${f.name}: larger than 20 MB`);
    else ok.push(i);
  });
  return { ok, refused };
}

/** "[2] Travel policy, page 3" */
export function citation(r: Pick<SearchResult, "cite" | "title" | "page" | "heading">): string {
  const where = [r.heading, r.page ? `page ${r.page}` : null].filter(Boolean).join(", ");
  return `[${r.cite}] ${r.title ?? "untitled"}${where ? `, ${where}` : ""}`;
}

export function chunkingLabel(kb: Pick<KnowledgeBaseOut, "chunking">): string {
  const s = STRATEGIES.find((x) => x.id === kb.chunking.strategy)?.label ?? kb.chunking.strategy;
  return `${s}, about ${kb.chunking.size} characters${kb.chunking.strategy === "fixed" ? ` (overlap ${kb.chunking.overlap})` : ""}`;
}

export function embeddingLabel(kb: Pick<KnowledgeBaseOut, "embedding">): string {
  const model = String(kb.embedding.model ?? "builtin");
  return model === "builtin" ? "Built-in (offline)" : model;
}

/** The block a workflow uses to search this knowledge base, ready to explain on the page. */
export function blockSnippet(name: string): string {
  return `Search knowledge → Knowledge base: ${name}`;
}

/** How often a knowledge base's web pages are fetched again, in the hours the page offers. */
export const RECRAWL_CHOICES: { hours: number | null; label: string }[] = [
  { hours: null, label: "Never" },
  { hours: 6, label: "Every 6 hours" },
  { hours: 24, label: "Daily" },
  { hours: 168, label: "Weekly" },
];

type Crawl = KnowledgeBaseOut["recrawl"]["last"];

/** The last re-crawl in one line: "2 updated, 1 added, 3 unchanged; 1 page failed". */
export function crawlSummary(last: Crawl): string {
  if (!last) return "Not fetched yet.";
  const parts = [
    last.updated && `${last.updated} updated`, last.added && `${last.added} added`, last.removed && `${last.removed} removed`,
    `${last.unchanged} unchanged`,
  ].filter(Boolean);
  const failed = last.failed.length;
  return `${parts.join(", ")}${failed ? `; ${failed} page${failed === 1 ? "" : "s"} failed` : ""}`;
}
