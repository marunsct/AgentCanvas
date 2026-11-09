/** "?" anywhere in the app (not while typing) opens the keyboard shortcut sheet, Settings › Keyboard shortcuts. */
import { useEffect } from "react";
import { useNavigate } from "react-router";

export const SHORTCUTS_PATH = "/settings/shortcuts";

/** Whether this key press asks for the shortcut sheet: "?" outside a field, without Ctrl, Cmd or Alt. */
export function isHelpKey(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "target">): boolean {
  if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return false;
  const target = e.target as HTMLElement | null;
  return !target?.closest?.("input, textarea, select, [contenteditable=''], [contenteditable='true'], .code-view, .cm-editor");
}

export function ShortcutKey() {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isHelpKey(e)) return;
      e.preventDefault();
      navigate(SHORTCUTS_PATH);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
  return null;
}
