/** The template gallery (S23): search, category chips and one card per starter template. */
import { useMemo, useState } from "react";
import type { TemplateInfo } from "../api/types";
import { categories, categoryOf, filterTemplates } from "./gallery";

export function TemplateGallery({ templates, busy, onPick }: {
  templates: TemplateInfo[];
  busy?: boolean;
  onPick: (t: TemplateInfo) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const cats = useMemo(() => categories(templates), [templates]);
  const shown = filterTemplates(templates, query, category);
  const clear = () => { setQuery(""); setCategory(null); };

  return (
    <div className="gallery" data-testid="template-gallery">
      <div className="gallery-tools">
        <input type="search" className="gallery-search" placeholder="Search templates (e.g. email, sql, approval)"
          aria-label="search templates" data-testid="template-search" value={query}
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setQuery(""); }} />
        <div className="gallery-chips" role="group" aria-label="Filter templates by category">
          <button type="button" className="gallery-chip" aria-pressed={category === null} onClick={() => setCategory(null)}
            data-testid="template-category-all">All <span className="count">{templates.length}</span></button>
          {cats.map((c) => (
            <button type="button" key={c.name} className="gallery-chip" aria-pressed={category === c.name}
              data-testid={`template-category-${c.name}`} onClick={() => setCategory(category === c.name ? null : c.name)}>
              {c.name} <span className="count">{c.count}</span>
            </button>
          ))}
        </div>
      </div>
      <p className="muted small" aria-live="polite" data-testid="template-count">
        {shown.length === templates.length ? `${templates.length} templates` : `${shown.length} of ${templates.length} templates`}
      </p>
      {shown.length === 0 ? (
        <div className="empty-state" data-testid="template-empty">
          No template matches{query ? <> “{query}”</> : null}{category ? <> in {category}</> : null}.{" "}
          <button type="button" className="link" onClick={clear}>Show all templates</button>
        </div>
      ) : (
        <ul className="cards gallery-cards">
          {shown.map((t) => (
            <li key={t.id}>
              <button type="button" className="card" data-testid={`template-${t.id}`} disabled={busy} onClick={() => onPick(t)}>
                <span className="card-kicker">{categoryOf(t)}</span>
                <b>{t.name}</b>
                <span className="muted">{t.description}</span>
                {!!t.tags?.length && <span className="card-tags">{t.tags.map((tag) => <span key={tag}>#{tag}</span>)}</span>}
                <span className="card-meta">
                  <small>{t.block_count || t.node_count} blocks</small>
                  {t.difficulty && <small className={`level ${t.difficulty}`}>{t.difficulty}</small>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
