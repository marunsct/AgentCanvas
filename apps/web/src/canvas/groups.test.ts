import { describe, expect, it } from "vitest";
import type { WF } from "../ir/model";
import { addGroup, frameAround, groupsOf, removeGroup, renameGroup } from "./groups";

const wf = (metadata: Record<string, unknown> = {}) => ({
  id: "wf", name: "t", kind: "workflow", state: { channels: [] }, resources: [], edges: [], metadata,
  nodes: [{ id: "a", type: "core.set", name: "a" }, { id: "b", type: "core.set", name: "b" }, { id: "c", type: "core.set", name: "c" }],
}) as unknown as WF;

describe("visual groups", () => {
  it("add, move members, rename and remove", () => {
    let w = wf({ other: 1 });
    w = wf(addGroup(w, "g1", "Intake", ["a", "b"]));
    expect(groupsOf(w)).toEqual([{ id: "g1", label: "Intake", color: "#2563eb", members: ["a", "b"] }]);
    w = wf(addGroup(w, "g2", "Answer", ["b", "c"])); // b moves to the new frame
    expect(groupsOf(w).map((g) => [g.id, g.members])).toEqual([["g1", ["a"]], ["g2", ["b", "c"]]]);
    expect(groupsOf(wf(renameGroup(w, "g1", "In")))[0].label).toBe("In");
    expect(groupsOf(wf(removeGroup(w, "g1"))).map((g) => g.id)).toEqual(["g2"]);
    expect(addGroup(w, "g3", "x", ["a"]).other).toBe(1); // other metadata is kept
  });

  it("drops deleted steps and empty frames", () => {
    const w = wf({ groups: [{ id: "g", label: "x", color: "#000", members: ["zz"] }, { id: "h", label: "y", color: "#000", members: ["a", "zz"] }] });
    expect(groupsOf(w)).toEqual([{ id: "h", label: "y", color: "#000", members: ["a"] }]);
  });

  it("frames member rectangles with padding and a label band", () => {
    expect(frameAround([{ x: 0, y: 0, width: 100, height: 50 }, { x: 200, y: 100, width: 100, height: 50 }]))
      .toEqual({ x: -24, y: -46, width: 348, height: 220 });
    expect(frameAround([])).toBeNull();
  });
});
