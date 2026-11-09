/** Keep a growing log scrolled to its end while the reader is at the end, and leave it alone once they scroll up
 * to read something earlier (a streaming answer used to pull them back down on every word). */
import { type UIEvent, useEffect, useRef } from "react";

/** Whether a scrolled box shows (about) its end. */
export function nearBottom(el: { scrollHeight: number; scrollTop: number; clientHeight: number }, slack = 48): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
}

/** `end` goes on an element after the last entry, `onScroll` on the scrolled box; `follow()` (say, on Send) scrolls
 * to the end again from the next change on. */
export function useFollowBottom(deps: readonly unknown[]) {
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const onScroll = (e: UIEvent<HTMLElement>) => { following.current = nearBottom(e.currentTarget); };
  /* oxlint-disable react-hooks/exhaustive-deps -- the caller's deps say when the log grew */
  useEffect(() => { // newer browsers return a promise here: an effect must not return it as its cleanup
    if (following.current) end.current?.scrollIntoView?.({ block: "end" });
  }, deps);
  /* oxlint-enable react-hooks/exhaustive-deps */
  return { end, onScroll, follow: () => { following.current = true; } };
}
