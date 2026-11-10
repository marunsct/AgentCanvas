/**
 * Presence (G-4): who else has this workflow open and which block each one has selected.
 *
 * Every tab posts a heartbeat (its selection and the workflow version it has) every few seconds and on change, and
 * reads the others from a Server-Sent-Events stream; the server keeps the rows in its database, so it works across
 * replicas. Nothing here edits the workflow: saving is still the editor's optimistic lock, and the 409 handling in
 * `store.ts` stays as it was. A peer whose version is newer than ours has saved: the bar says so.
 */
import { useEffect } from "react";
import { create } from "zustand";
import { apiFetch } from "../api/client";
import { getStream } from "../run/sse";
import { useEditor } from "./store";

export interface Peer {
  session: string;
  user_id: string;
  name: string;
  selected: string | null;
  version: number | null;
}

export const HEARTBEAT_MS = 10_000;
const COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#db2777", "#65a30d"];

export const usePresence = create<{ session: string; peers: Peer[] }>(() => ({ session: "", peers: [] }));

/** A colour per person, the same for everyone who sees them. */
export function colorOf(userId: string): string {
  let h = 0;
  for (const ch of userId) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}

/** "Ada Lovelace" -> "AL", "ada@x.org" -> "A". */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[words.length - 1][0] : (words[0] ?? "?").slice(0, 1);
  return letters.toUpperCase();
}

export interface Here {
  user_id: string;
  name: string;
  selected: string[]; // the blocks this person has selected, in their tabs
}

/** The people here besides this tab, one entry per person (several tabs of one person count once; your own other
 * tabs show as "You, in another tab"). */
export function others(peers: Peer[], session: string, me: string): Here[] {
  const by = new Map<string, Here>();
  for (const p of peers) {
    if (p.session === session) continue;
    const entry = by.get(p.user_id) ?? { user_id: p.user_id, name: p.user_id === me ? "You, in another tab" : p.name, selected: [] };
    if (p.selected && !entry.selected.includes(p.selected)) entry.selected.push(p.selected);
    by.set(p.user_id, entry);
  }
  return [...by.values()];
}

/** Who has selected block `id`, for its badge on the canvas. */
export function selectingBlock(peers: Peer[], session: string, id: string): Peer[] {
  return peers.filter((p) => p.session !== session && p.selected === id);
}

/** The peers who saved a newer version than `version`: the newest one first. */
export function savedNewer(peers: Peer[], session: string, version: number): Peer[] {
  return peers
    .filter((p) => p.session !== session && p.version !== null && p.version > version)
    .sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
}

function newSession(): string {
  const c = globalThis.crypto;
  return c?.randomUUID ? c.randomUUID() : `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

/** Keep this tab's presence on `workflowId` for as long as the editor shows it. */
export function usePresenceChannel(workflowId: string | undefined): void {
  useEffect(() => {
    if (!workflowId) return;
    const session = newSession();
    usePresence.setState({ session, peers: [] });
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const set = (peers: Peer[]) => { if (!abort.signal.aborted) usePresence.setState({ peers }); };
    const beat = async () => {
      const { selected, version } = useEditor.getState();
      try {
        const out = await apiFetch<{ peers: Peer[] }>(`/workflows/${workflowId}/presence`, {
          method: "POST", json: { session, selected, version: version || null }, signal: abort.signal,
        });
        set(out.peers);
      } catch { /* presence is a courtesy: the editor works without it */ }
    };
    const soon = () => { // a change (a new selection, a saved version) is told within a moment, not at the next beat
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void beat(), 250);
    };
    void beat();
    const interval = setInterval(() => void beat(), HEARTBEAT_MS);
    const unsubscribe = useEditor.subscribe((s, prev) => { if (s.selected !== prev.selected || s.version !== prev.version) soon(); });
    // The stream is open only while the tab is shown. Over HTTP/1.1 a browser has 6 connections per site for all
    // its tabs, and a stream holds one for as long as it is open: a few editor tabs in the background used them
    // all, and every other request of every tab then waited. A hidden tab keeps its heartbeat (short requests, so
    // the others still see it here) and hears who is here again when it is shown.
    let stream: AbortController | null = null;
    const open = () => {
      if (stream || abort.signal.aborted) return;
      const mine = (stream = new AbortController());
      void (async () => { // reconnect after a drop, unless the tab was hidden or the editor moved on
        while (!mine.signal.aborted) {
          try {
            await getStream<{ peers: Peer[] }>(`/workflows/${workflowId}/presence/stream`, (m) => set(m.peers), mine.signal);
          } catch { /* reconnect below */ }
          if (!mine.signal.aborted) await new Promise((r) => setTimeout(r, 3000));
        }
      })();
    };
    const close = () => { stream?.abort(); stream = null; };
    const onVisibility = () => {
      if (document.hidden) close();
      else { open(); void beat(); }
    };
    if (!document.hidden) open();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      abort.abort();
      close();
      document.removeEventListener("visibilitychange", onVisibility);
      clearInterval(interval);
      if (timer) clearTimeout(timer);
      unsubscribe();
      usePresence.setState({ peers: [] });
      void apiFetch(`/workflows/${workflowId}/presence/${session}`, { method: "DELETE" }).catch(() => undefined);
    };
  }, [workflowId]);
}
