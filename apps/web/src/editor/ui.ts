/** Transient UI state (command palette, dock tab). */
import { create } from "zustand";

export type DockTab = "problems" | "timeline" | "explain";

interface UiState {
  paletteOpen: boolean;
  paletteAt: { x: number; y: number } | null; // flow position for "add node here"
  dock: DockTab;
  diff: { against: string; added: string[]; changed: string[] } | null; // canvas colours vs a version (M6-T1)
  openPalette: (at?: { x: number; y: number } | null) => void;
  closePalette: () => void;
  setDock: (d: DockTab) => void;
  setDiff: (d: UiState["diff"]) => void;
}

export const useUi = create<UiState>((set) => ({
  paletteOpen: false,
  paletteAt: null,
  dock: "problems",
  openPalette: (at = null) => set({ paletteOpen: true, paletteAt: at }),
  closePalette: () => set({ paletteOpen: false, paletteAt: null }),
  setDock: (dock) => set({ dock }),
  diff: null,
  setDiff: (diff) => set({ diff }),
}));
