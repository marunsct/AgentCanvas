import { describe, expect, it } from "vitest";
import { triage } from "../test/fixtures";
import { applyAll, applyLocal, CommandError, mergePatch } from "./commands";

describe("mergePatch (RFC 7396)", () => {
  it("merges, deletes on null and replaces arrays", () => {
    expect(mergePatch({ a: 1, b: { c: 2, d: 3 } }, { b: { c: null, e: 4 }, f: [1] })).toEqual({ a: 1, b: { d: 3, e: 4 }, f: [1] });
  });
});

describe("applyLocal mirrors the server commands", () => {
  it("RemoveNode drops the node and its edges", () => {
    const wf = applyLocal(triage(), { op: "RemoveNode", id: "n_ag" });
    expect(wf.nodes.find((n) => n.id === "n_ag")).toBeUndefined();
    expect(wf.edges.some((e) => e.source.node === "n_ag" || e.target.node === "n_ag")).toBe(false);
  });

  it("AddNode + Connect + UpdateNode", () => {
    const wf = applyAll(triage(), [
      { op: "AddNode", node: { id: "n_x", type: "core.set", name: "x", config: { values: {} } } },
      { op: "Connect", edge: { id: "ex", kind: "flow", source: { node: "n_x" }, target: { node: "END" } } },
      { op: "UpdateNode", id: "n_x", patch: { config: { values: { summary: "hi" } }, label: "X" } },
    ]);
    const x = wf.nodes.find((n) => n.id === "n_x")!;
    expect(x.label).toBe("X");
    expect(x.config).toEqual({ values: { summary: "hi" } });
    expect(wf.edges.at(-1)?.id).toBe("ex");
  });

  it("rejects duplicate ids and unknown targets", () => {
    expect(() => applyLocal(triage(), { op: "AddNode", node: { id: "n_cls", type: "core.set", name: "dup" } })).toThrow(CommandError);
    expect(() => applyLocal(triage(), { op: "Disconnect", id: "nope" })).toThrow(/unknown edge/);
  });

  it("RemoveResource removes its wiring", () => {
    const wf = applyLocal(triage(), { op: "RemoveResource", id: "r_fast" });
    expect(wf.edges.some((e) => e.source.node === "r_fast")).toBe(false);
  });
});
