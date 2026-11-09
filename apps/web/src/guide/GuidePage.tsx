/** The user guide, in the app: the same pages the Copilot searches, so its answers can link to the section they came
 * from ("From: Guide › Models › Local models"). Links between pages stay in the app. */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { NavLink, useLocation, useNavigate, useParams } from "react-router";
import { apiFetch } from "../api/client";
import type { GuidePage as Page, GuidePageRef } from "../api/generated";
import { Markdown } from "../ui/Markdown";
import { QueryStatus } from "../ui/QueryStatus";

/** `deploy.md#tls` -> `/guide/deploy#tls`; a link within the page stays as it is. */
export function guideLink(href: string): string {
  const m = /^([a-z0-9-]+)\.md(#[\w-]+)?$/.exec(href);
  return m ? `/guide/${m[1]}${m[2] ?? ""}` : href;
}

export function GuidePage() {
  const { page = "index" } = useParams();
  const { hash } = useLocation();
  const navigate = useNavigate();
  const pages = useQuery({ queryKey: ["guide"], queryFn: () => apiFetch<GuidePageRef[]>("/guide"), staleTime: Infinity });
  const doc = useQuery({ queryKey: ["guide", page], queryFn: () => apiFetch<Page>(`/guide/${page}`), staleTime: Infinity });
  useEffect(() => {  // a link to a section: scroll to it once the page is there
    if (!doc.data) return;
    if (hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [doc.data, hash]);
  return (
    <div className="page guide-page">
      <div className="settings-layout">
        <nav className="settings-rail" aria-label="guide pages">
          <div className="rail-group">
            <span className="rail-heading">User guide</span>
            {(Array.isArray(pages.data) ? pages.data : []).map((p) => (
              <NavLink key={p.page} to={`/guide/${p.page}`} className={({ isActive }) => (isActive || (page === p.page) ? "active" : "")}>
                {p.title}</NavLink>
            ))}
          </div>
        </nav>
        <article className="settings-content guide-content" data-testid="guide"
          onClick={(e) => {  // links to other pages of the guide move within the app, without reloading it
            const a = (e.target as HTMLElement).closest("a");
            const href = a?.getAttribute("href");
            if (href?.startsWith("/guide/") && !e.metaKey && !e.ctrlKey) { e.preventDefault(); void navigate(href); }
          }}>
          <QueryStatus q={doc} what="this page of the guide" />
          {doc.data && <div className="settings-card"><Markdown page text={doc.data.markdown} link={guideLink} /></div>}
        </article>
      </div>
    </div>
  );
}
