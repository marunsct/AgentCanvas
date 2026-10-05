/** Transient UI state (command palette, dock tab). */
import { create } from "zustand";

export type DockTab = "problems" | "timeline" | "explain";

interface UiState {
  paletteOpen: boolean;
  paletteAt: { x: number; y: number } | null; // flow position for "add node here"
  dock: DockTab;
  diff: { against: string; added: string[]; changed: string[] } | null; // canvas colours vs a version (M6-T1)
  blast: boolean; // blast-radius overlay (FR-X-02)
  dryRun: boolean; // test runs stub risky tools (FR-X-02)
  openPalette: (at?: { x: number; y: number } | null) => void;
  closePalette: () => void;
  setDock: (d: DockTab) => void;
  setDiff: (d: UiState["diff"]) => void;
  toggleBlast: () => void;
  setDryRun: (on: boolean) => void;
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
  blast: false,
  toggleBlast: () => set((s) => ({ blast: !s.blast })),
  dryRun: false,
  setDryRun: (dryRun) => set({ dryRun }),
}));
