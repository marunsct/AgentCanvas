import { describe, expect, it } from "vitest";
import { SIMPLE_TYPES } from "../editor/simple";
import { manifests, triage } from "../test/fixtures";
import { suggest } from "./suggest";

describe("suggest in Simple mode (W-12)", () => {
  it("offers only the blocks Simple mode shows", () => {
    const wf = triage();
    const next = suggest(wf, manifests, { node: "START", handle: "next", side: "out" }, true)!;
    expect(next.add.length).toBeGreaterThan(0);
    expect(next.add.filter((c) => !SIMPLE_TYPES.has(c.type))).toEqual([]); // subgraphs, scripts, framework agents leaked in
    const model = suggest(wf, manifests, { node: "n_ag", handle: "model", side: "in" }, true)!;
    expect(model.add.every((c) => SIMPLE_TYPES.has(c.type))).toBe(true);
    expect(suggest(wf, manifests, { node: "START", handle: "next", side: "out" })!.add.length).toBeGreaterThan(next.add.length);
  });
});
