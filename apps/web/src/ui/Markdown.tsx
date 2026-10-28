/** A small Markdown renderer for the Copilot's answers: paragraphs, headings, lists, block quotes, fenced code (with
 * Copy), inline code, bold, italics and links. Everything becomes React elements (no HTML is passed through, so a
 * reply cannot inject markup), and links open only http(s), mailto and in-app paths. */
import { useState } from "react";

type Block =
  | { kind: "p" | "quote"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] }
  | { kind: "code"; lang: string; text: string };

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

const SAFE_LINK = /^(https?:\/\/|mailto:|\/(?!\/))/i;

/** Inline Markdown: `code`, **bold**, *italic* / _italic_, [text](url). */
export function Inline({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))|(\*[^*\s][^*]*\*)|(\b_[^_\s][^_]*_\b)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) parts.push(<code key={key++}>{t.slice(1, -1)}</code>);
    else if (m[2]) parts.push(<strong key={key++}>{t.slice(2, -2)}</strong>);
    else if (m[3]) {
      const [, label, href] = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t)!;
      parts.push(SAFE_LINK.test(href)
        ? <a key={key++} href={href} target={href.startsWith("/") ? undefined : "_blank"} rel="noopener noreferrer">{label}</a>
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

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {parseBlocks(text).map((b, i) => {
        switch (b.kind) {
          case "code": return <CodeBlock key={i} lang={b.lang} text={b.text} />;
          case "h": return b.level <= 2 ? <h4 key={i}><Inline text={b.text} /></h4> : <h5 key={i}><Inline text={b.text} /></h5>;
          case "ul": return <ul key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ul>;
          case "ol": return <ol key={i}>{b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ol>;
          case "quote": return <blockquote key={i}><Inline text={b.text} /></blockquote>;
          default: return <p key={i}>{b.text.split("\n").map((ln, j) => <span key={j}>{j > 0 && <br />}<Inline text={ln} /></span>)}</p>;
        }
      })}
    </div>
  );
}
