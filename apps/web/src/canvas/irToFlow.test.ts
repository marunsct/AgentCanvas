import { describe, expect, it } from "vitest";
import { manifests, triage } from "../test/fixtures";
import { irToFlow } from "./irToFlow";

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
});
