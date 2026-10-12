import { describe, expect, it } from "vitest";
import { blockRange, changedLines } from "./CodePanel";

describe("changedLines", () => {
  it("marks new and modified lines only", () => {
    const prev = ["a", "b", "c", ""];
    const next = ["a", "b2", "c", "", "d"];
    expect([...changedLines(prev, next)]).toEqual([1, 4]);
  });
});

describe("blockRange", () => {
  it("ends the last block at the flow engine's FLOW (Python)", () => {
    const code = ["# ── @canvas-node a \"A\" (core.llm) ──", "async def a(run):", "    pass", "", "# ── @canvas-node b \"B\" (core.llm) ──",
      "async def b(run):", "    pass", "", "FLOW = rt.Flow(", ")"];
    expect(blockRange(code, "a")).toEqual([0, 4]);
    expect(blockRange(code, "b")).toEqual([4, 8]);
  });

  it("ends the last block at export const FLOW (TypeScript), CANVAS and build()", () => {
    const ts = ["// ── @canvas-node b \"B\" ──", "const b = rt.step();", "", "export const FLOW: rt.Flow = {", "};"];
    expect(blockRange(ts, "b")).toEqual([0, 3]);
    const crew = ["    # ── @canvas-node b \"B\" ──", "    def b(self): pass", "", "CANVAS = rt.Canvas(", ")"];
    expect(blockRange(crew, "b")).toEqual([0, 3]);
    const adk = ["# ── @canvas-node b \"B\" ──", "async def b(run): pass", "", "def build() -> Workflow:", "    pass"];
    expect(blockRange(adk, "b")).toEqual([0, 3]);
  });

  it("is empty when nothing is selected or the block is not in the code", () => {
    expect(blockRange(["x"], null)).toEqual([-1, -1]);
    expect(blockRange(["x"], "a")).toEqual([-1, -1]);
  });
});
