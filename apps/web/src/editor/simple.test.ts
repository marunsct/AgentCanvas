import { describe, expect, it } from "vitest";
import { loadSimple, paletteTypes, saveSimple, SIMPLE_TABS, SIMPLE_TYPES } from "./simple";

describe("simple mode", () => {
  it("keeps the beginner blocks only", () => {
    const all = ["core.llm", "core.agent", "core.subgraph", "core.map", "mw.pii", "model.chat", "agent.a2a"].map((type) => ({ type }));
    expect(paletteTypes(all, true).map((m) => m.type)).toEqual(["core.llm", "core.agent", "model.chat"]);
    expect(paletteTypes(all, false)).toHaveLength(all.length);
    expect(SIMPLE_TYPES.has("core.router")).toBe(true);
  });
  it("hides blocks, not publishing", () => {
    expect(SIMPLE_TABS.has("versions")).toBe(true);
    expect(SIMPLE_TABS.has("code")).toBe(false);
  });
  it("still works without browser storage", () => {
    saveSimple(true);  // no localStorage here: not remembered, and no error
    expect(loadSimple()).toBe(false);
  });
});
