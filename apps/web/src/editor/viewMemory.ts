/** Where the canvas was (zoom, position, selected block) when you left a workflow for its Workspace, so coming back
 * (with the Back link or the browser's Back button) puts you in the same place (S17). Per tab, best effort. */
import type { Viewport } from "@xyflow/react";

export interface SavedView {
  viewport?: Viewport;
  selected?: string | null;
}

const key = (wfId: string) => `agentcanvas.view.${wfId}`;

export function rememberView(wfId: string, view: SavedView): void {
  try { sessionStorage.setItem(key(wfId), JSON.stringify(view)); } catch { /* storage off: nothing to restore */ }
}

export function recallView(wfId: string): SavedView | null {
  try {
    const raw = sessionStorage.getItem(key(wfId));
    return raw ? (JSON.parse(raw) as SavedView) : null;
  } catch {
    return null;
  }
}
