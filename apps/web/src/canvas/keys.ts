/** Keyboard activation shared by palette items and port handles: Enter or Space, like a button. */
import type { KeyboardEvent } from "react";

export function isActivate(e: Pick<KeyboardEvent, "key">): boolean {
  return e.key === "Enter" || e.key === " ";
}

/** Run `fn` on Enter/Space and keep the page from scrolling or React Flow from handling the key. */
export function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (!isActivate(e)) return;
    e.preventDefault();
    e.stopPropagation();
    fn();
  };
}
