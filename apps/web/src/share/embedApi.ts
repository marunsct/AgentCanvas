/** The chat page's postMessage API for the site that frames it (G-15, widget.js or a hand-made iframe).
 *
 * Every message is an object with `agentcanvas: 1` and a `type`. The page accepts from its parent window only, and
 * only from a site the link allows (its "sites that may show the chat widget"; none listed: any site, as for framing).
 * It sends to its parent, addressed to the parent's own origin when known.
 *
 *   parent -> page   { agentcanvas: 1, type: "prefill", text }   put text in the message box and focus it
 *   page -> parent   { agentcanvas: 1, type: "ready" }           the page has loaded
 *   page -> parent   { agentcanvas: 1, type: "reply", text, runId, threadId, question? }   an answer arrived
 */

export const PROTOCOL = 1;
export const MAX_PREFILL = 20_000;

export type EmbedCommand = { agentcanvas: 1; type: "prefill"; text: string };
export type EmbedEvent =
  | { type: "ready" }
  | { type: "reply"; text: string; runId?: string; threadId?: string; question?: boolean };

/** Does `origin` match one of the sites a link allows (`https://example.com`, `https://*.example.com`)? No list: any. */
export function originAllowed(origin: string, allowed: readonly string[] | undefined | null): boolean {
  if (!allowed || allowed.length === 0) return true;
  let url: URL;
  try {
    url = new URL(origin);
  } catch {
    return false;
  }
  if (url.origin === "null" || url.origin !== origin) return false; // an origin, not a page address
  return allowed.some((entry) => {
    const wild = /^(https?:)\/\/\*\.(.+)$/.exec(entry);
    if (!wild) return entry.toLowerCase() === origin.toLowerCase();
    const [, protocol, rest] = wild;
    const host = rest.includes(":") ? url.host : url.hostname; // `*.example.com:8443` names a port
    return url.protocol === protocol && host.toLowerCase().endsWith(`.${rest.toLowerCase()}`);
  });
}

/** A command from the parent, or null for anything else (other scripts post to windows too). */
export function parseCommand(data: unknown): EmbedCommand | null {
  if (!data || typeof data !== "object") return null;
  const d = data as { agentcanvas?: unknown; type?: unknown; text?: unknown };
  if (d.agentcanvas !== PROTOCOL) return null;
  if (d.type === "prefill" && typeof d.text === "string") return { agentcanvas: 1, type: "prefill", text: d.text.slice(0, MAX_PREFILL) };
  return null;
}

/** The origin of the page that frames this one: Chrome and Safari say it; elsewhere the referrer does. */
export function parentOrigin(): string | null {
  const ancestors = (location as unknown as { ancestorOrigins?: ArrayLike<string> }).ancestorOrigins;
  if (ancestors && ancestors.length > 0) return ancestors[0];
  try {
    return document.referrer ? new URL(document.referrer).origin : null;
  } catch {
    return null;
  }
}

/** Tell the framing page something happened. Nothing is sent to a site the link does not allow; when the framing
 * site cannot be known and the link names sites, nothing is sent either. */
export function postToParent(event: EmbedEvent, allowed: readonly string[] | undefined | null): boolean {
  if (window.parent === window) return false; // not framed
  const origin = parentOrigin();
  if (origin !== null && !originAllowed(origin, allowed)) return false;
  if (origin === null && allowed && allowed.length > 0) return false;
  window.parent.postMessage({ agentcanvas: PROTOCOL, ...event }, origin ?? "*");
  return true;
}
