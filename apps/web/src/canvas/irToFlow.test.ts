import { describe, expect, it } from "vitest";
import { manifests, triage } from "../test/fixtures";
import { irToFlow, sameData } from "./irToFlow";

describe("irToFlow", () => {
  it("creates START/END, one node per IR node and resource, with typed handles", () => {
    const { nodes, edges } = irToFlow(triage(), {}, manifests);
    expect(nodes).toHaveLength(2 + 5 + 3);
    expect(nodes.find((n) => n.id === "n_rt")?.data.routes).toEqual(["high", "normal"]);
    const route = edges.find((e) => e.id === "e3")!;
    expect([route.sourceHandle, route.targetHandle, route.label]).toEqual(["route:high", "in", "high"]);
    const wire = edges.find((e) => e.id === "w1")!;
    expect([wire.sourceHandle, wire.targetHandle]).toEqual(["model", "model"]);
  });

  it("uses saved positions and diagnostics/status badges", () => {
    const { nodes } = irToFlow(triage(), { n_cls: { x: 5, y: 7 } }, manifests,
      [{ code: "E001", severity: "error", message: "x", node_id: "n_cls" }], { n_cls: "running" });
    const cls = nodes.find((n) => n.id === "n_cls")!;
    expect(cls.position).toEqual({ x: 5, y: 7 });
    expect([cls.data.errors, cls.data.status]).toEqual([1, "running"]);
  });

  it("tells the canvas which nodes did not change, so only those that did re-render (W-9)", () => {
    const wf = triage();
    const before = irToFlow(wf, {}, manifests, [], { n_cls: "running" });
    const after = irToFlow(wf, {}, manifests, [], { n_cls: "ok" }, null, { output: { n_ag: "Hel" } });
    const changed = after.nodes.filter((n) => !sameData(before.nodes.find((b) => b.id === n.id)!.data, n.data)).map((n) => n.id);
    expect(changed).toEqual(["n_cls", "n_ag"]); // the rest keep their data object (a new one every time made memo useless)
    expect(after.nodes.find((n) => n.id === "n_rt")!.position).toEqual(before.nodes.find((n) => n.id === "n_rt")!.position);
  });
});

describe("sameData", () => {
  it("compares arrays and plain objects by their contents", () => {
    expect(sameData({ a: [1, { b: "x" }], c: undefined }, { a: [1, { b: "x" }], c: undefined })).toBe(true);
    expect(sameData({ a: [1, 2] }, { a: [1, 3] })).toBe(false);
    expect(sameData({ a: 1 }, { a: 1, b: 2 })).toBe(false);
    expect(sameData([1], { 0: 1 })).toBe(false);
  });
});
