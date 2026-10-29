/** Transient UI state (command palette, dock tab). */
import { create } from "zustand";
import type { PortRef } from "../canvas/suggest";
import { savePreference } from "../api/preferences";
import { loadSimple, saveSimple } from "./simple";

export type DockTab = "problems" | "timeline" | "explain" | "runs";

interface UiState {
  paletteOpen: boolean;
  paletteAt: { x: number; y: number } | null; // flow position for "add node here"
  dock: DockTab;
  diff: { against: string; added: string[]; changed: string[] } | null; // canvas colours vs a version (M6-T1)
  blast: boolean; // blast-radius overlay (FR-X-02)
  dryRun: boolean; // test runs stub risky tools (FR-X-02)
  portMenu: (PortRef & { x: number; y: number }) | null; // "what can connect here?" for a clicked port (screen x/y)
  help: boolean; // the beginner guide drawer
  simple: boolean; // Simple mode: beginner blocks and panels only (S31)
  openPalette: (at?: { x: number; y: number } | null) => void;
  closePalette: () => void;
  setDock: (d: DockTab) => void;
  setDiff: (d: UiState["diff"]) => void;
  toggleBlast: () => void;
  setDryRun: (on: boolean) => void;
  openPortMenu: (m: UiState["portMenu"]) => void;
  setHelp: (on: boolean) => void;
  setSimple: (on: boolean) => void;
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
  portMenu: null,
  openPortMenu: (portMenu) => set({ portMenu }),
  help: false,
  setHelp: (help) => set({ help }),
  simple: loadSimple(),
  setSimple: (simple) => { saveSimple(simple); set({ simple }); savePreference({ simple_mode: simple }); },
}));
