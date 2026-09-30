import { describe, expect, it } from "vitest";
import { changedLines } from "./CodePanel";

describe("changedLines", () => {
  it("marks new and modified lines only", () => {
    const prev = ["a", "b", "c", ""];
    const next = ["a", "b2", "c", "", "d"];
    expect([...changedLines(prev, next)]).toEqual([1, 4]);
  });
});
