/** Preferences that follow the person between browsers (the theme and the editor's Simple or Full mode): applied
 * when `/me` answers, saved on change. Saving is best effort: a failure keeps the choice in this browser. */
import { apiFetch } from "./client";
import type { Preferences } from "./generated";

let applying = false; // applying the saved ones must not save them back

export function savePreference(patch: Preferences): void {
  if (applying) return;
  void apiFetch("/me/preferences", { method: "PUT", json: patch }).catch(() => undefined);
}

export async function applyPreferences(p: Preferences | undefined): Promise<void> {
  if (!p) return;
  applying = true;
  try {
    if (p.theme) (await import("../theme/theme")).useTheme.getState().setTheme(p.theme);
    if (typeof p.simple_mode === "boolean") (await import("../editor/ui")).useUi.getState().setSimple(p.simple_mode);
  } finally {
    applying = false;
  }
}
