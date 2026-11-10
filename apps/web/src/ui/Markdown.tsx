/** A small Markdown renderer for the Copilot's answers and the user guide: paragraphs, headings, lists, block quotes,
 * tables, fenced code (with Copy), inline code, bold, italics and links. Everything becomes React elements (no HTML is
 * passed through, so a reply cannot inject markup), and links open only http(s), mailto and in-app paths. */
import { createContext, useContext, useState } from "react";

type Block =
  | { kind: "p" | "quote"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "code"; lang: string; text: string };

const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
const RULE = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

/** "Getting started!" -> "getting-started": a heading's anchor, the way MkDocs makes it. */
export const slug = (text: string) => text.toLowerCase().replace(/[`*_[\]()]/g, "").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i++; // the closing fence (or the end: an unclosed block still shows, as code)
      out.push({ kind: "code", lang: fence[1], text: body.join("\n") });
      continue;
    }
    if (!line.trim()) { i++; continue; }
    if (line.trim().startsWith("|") && i + 1 < lines.length && RULE.test(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      out.push({ kind: "table", head, rows });
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) { out.push({ kind: "h", level: heading[1].length, text: heading[2] }); i++; continue; }
    const listKind = (l: string) => (/^\s*[-*+]\s+/.test(l) ? "ul" : /^\s*\d+[.)]\s+/.test(l) ? "ol" : null);
    const kind = listKind(line);
    if (kind) {
      const items: string[] = [];
      while (i < lines.length && listKind(lines[i]) === kind) {
        let item = lines[i].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "");
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listKind(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      out.push({ kind, items });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ kind: "quote", text: body.join(" ") });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*```/.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
      && !listKind(lines[i]) && !/^\s*>/.test(lines[i])) para.push(lines[i++]);
    out.push({ kind: "p", text: para.join("\n") });
  }
  return out;
}

const EXTERNAL_LINK = /^(https?:\/\/|mailto:)/i;

/** Whether a link from model or guide text may be followed: an in-app path (same tab), a web or mail address (new
 * tab), or neither (shown as plain text). A path counts as in-app only when the browser resolves it to this site:
 * `/\host` reads like a path but the URL parser treats the backslash as a slash and leaves for `host`. */
export function linkKind(href: string): "internal" | "external" | null {
  if (href.startsWith("#")) return "internal";
  if (EXTERNAL_LINK.test(href)) return "external";
  if (!href.startsWith("/") || href.includes("\\")) return null;
  const origin = globalThis.location?.origin ?? "http://localhost";
  try {
    return new URL(href, origin).origin === origin ? "internal" : null;
  } catch {
    return null;
  }
}

/** How links are resolved: the guide turns `deploy.md#section` into its own page's address. */
const LinkRewrite = createContext<((href: string) => string) | null>(null);

/** Inline Markdown: `code`, **bold**, *italic* / _italic_, [text](url). */
export function Inline({ text }: { text: string }) {
  const rewrite = useContext(LinkRewrite);
  const parts: React.ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(!?\[[^\]]+\]\([^)\s]+\))|(\*[^*\s][^*]*\*)|(\b_[^_\s][^_]*_\b)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) parts.push(<code key={key++}>{t.slice(1, -1)}</code>);
    else if (m[2]) parts.push(<strong key={key++}>{t.slice(2, -2)}</strong>);
    else if (m[3] && t.startsWith("!")) parts.push(<em key={key++}>{/^!\[([^\]]+)\]/.exec(t)![1]}</em>); // an image: its description
    else if (m[3]) {
      const [, label, raw] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t)!;
      const href = rewrite ? rewrite(raw) : raw;
      const kind = linkKind(href);
      parts.push(kind
        ? <a key={key++} href={href} target={kind === "internal" ? undefined : "_blank"} rel="noopener noreferrer">{label}</a>
        : label);
    } else parts.push(<em key={key++}>{t.slice(1, -1)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="md-code">
      <div className="md-code-bar">
        <span>{lang || "code"}</span>
        <button type="button" className="link small" onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
        }}>{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre><code>{text}</code></pre>
    </div>
  );
}

/** `page`: a document (the guide), with its own heading levels, anchors to link to, and `link` to resolve its links;
 * otherwise a chat answer, whose headings stay small. */
export function Markdown({ text, page = false, link }: { text: string; page?: boolean; link?: (href: string) => string }) {
  return (
    <LinkRewrite.Provider value={link ?? null}>
    <div className={page ? "md md-page" : "md"}>
      {parseBlocks(text).map((b, i) => {
        switch (b.kind) {
          case "code": return <CodeBlock key={i} lang={b.lang} text={b.text} />;
          case "h": {
            if (page) {
              const H = (["h1", "h2", "h3", "h4"] as const)[b.level - 1];
              return <H key={i} id={slug(b.text)}><Inline text={b.text} /></H>;
            }
            return b.level <= 2 ? <h4 key={i}><Inline text={b.text} /></h4> : <h5 key={i}><Inline text={b.text} /></h5>;
          }
          case "table": return (
            <div key={i} className="md-table"><table className="table">
              <thead><tr>{b.head.map((h, j) => <th key={j}><Inline text={h} /></th>)}</tr></thead>
              <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}><Inline text={c} /></td>)}</tr>)}</tbody>
            </table></div>
          );
          case "ul": return <ul key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ul>;
          case "ol": return <ol key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ol>;
          case "quote": return <blockquote key={i}><Inline text={b.text} /></blockquote>;
          // a document's lines are wrapped source (a line ends a line only with two trailing spaces); a chat answer's are not
          default: return <p key={i}>{(page ? b.text.split(/ {2,}\n/).map((l) => l.replace(/\s*\n\s*/g, " ")) : b.text.split("\n"))
            .map((ln, j) => <span key={j}>{j > 0 && <br />}<Inline text={ln} /></span>)}</p>;
        }
      })}
    </div>
    </LinkRewrite.Provider>
  );
}
