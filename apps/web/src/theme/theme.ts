/** Colour themes: light, medium (a dimmed slate) and dark, or follow the system. Kept in this browser only. */
import { create } from "zustand";

export type Theme = "system" | "light" | "medium" | "dark";
export const THEMES: { id: Theme; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "medium", label: "Medium" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];
const KEY = "agentcanvas.theme";

function stored(): Theme {
  try {
    const t = localStorage.getItem(KEY);
    if (t && THEMES.some((x) => x.id === t)) return t as Theme;
  } catch { /* storage blocked: use the default */ }
  return "system";
}

const dark = () => typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;

/** The theme actually shown: "system" resolves to light or dark. */
export function resolved(theme: Theme): Exclude<Theme, "system"> {
  return theme === "system" ? (dark() ? "dark" : "light") : theme;
}

export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const shown = resolved(theme);
  root.dataset.theme = shown;
  root.style.colorScheme = shown === "light" ? "light" : "dark";
}

interface ThemeState { theme: Theme; setTheme: (t: Theme) => void }

export const useTheme = create<ThemeState>((set) => ({
  theme: stored(),
  setTheme: (theme) => {
    try { localStorage.setItem(KEY, theme); } catch { /* not remembered, still applied */ }
    applyTheme(theme);
    set({ theme });
  },
}));

/** Apply the remembered theme before the first paint, and follow the system while "system" is chosen. */
export function initTheme() {
  applyTheme(useTheme.getState().theme);
  if (typeof matchMedia === "function") {
    matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
      if (useTheme.getState().theme === "system") applyTheme("system");
    });
  }
}
