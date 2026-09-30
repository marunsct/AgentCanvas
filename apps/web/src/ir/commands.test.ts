import { describe, expect, it } from "vitest";
import { triage } from "../test/fixtures";
import { applyAll, applyLocal, CommandError, composePatches, mergePatch } from "./commands";

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

describe("composePatches (queued form edits)", () => {
  const doc = { config: { values: { a: "1" }, rf: { properties: { field_1: { type: "string" } } } } };
  const seq = (...ps: unknown[]) => ps.reduce((d, p) => mergePatch(d, p), doc);

  it("has the effect of applying both patches, deletions included", () => {
    const p1 = { config: { values: { b: "2" } } };
    const p2 = { config: { values: { a: null } } };
    expect(mergePatch(doc, composePatches(p1, p2))).toEqual(seq(p1, p2));
  });

  it("refuses to compose a delete followed by an object (no single merge patch does that)", () => {
    const p1 = { config: { rf: null } };
    const p2 = { config: { rf: { properties: { urgency: { type: "string" } } } } };
    expect(composePatches(p1, p2)).toBeUndefined();
    expect(seq(p1, p2)).toEqual({ config: { values: { a: "1" }, rf: { properties: { urgency: { type: "string" } } } } });
  });
});
