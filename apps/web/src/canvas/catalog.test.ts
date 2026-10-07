import { describe, expect, it } from "vitest";
import { reorderCommands, wiredEdges } from "../inspector/WiredPorts";
import { applyAll } from "../ir/commands";
import type { WF } from "../ir/model";
import { manifests, triage } from "../test/fixtures";
import { checkConnection, flowPort, nextOrder } from "./connect";
import { irToFlow } from "./irToFlow";

const c = (source: string, sourceHandle: string | null, target: string, targetHandle: string | null) =>
  ({ source, sourceHandle, target, targetHandle });

/** triage + a map node, two middleware and a fallback middleware that takes models (Sprint 5 catalog). */
function catalog(): WF {
  const wf = triage();
  wf.nodes.push({ id: "n_map", type: "core.map", name: "each_ticket", config: { items: "ticket", item: "ticket" } });
  wf.resources.push(
    { id: "r_limit", type: "mw.model_call_limit", name: "limit", config: {} },
    { id: "r_retry", type: "mw.model_retry", name: "retry", config: {} },
    { id: "r_fb", type: "mw.model_fallback", name: "fallback", config: {} },
  );
  wf.edges.push(
    { id: "w_l", kind: "wiring", source: { node: "r_limit" }, target: { node: "n_ag", port: "middleware" }, order: 0 },
    { id: "w_r", kind: "wiring", source: { node: "r_retry" }, target: { node: "n_ag", port: "middleware" }, order: 1 },
    { id: "f_each", kind: "flow", source: { node: "n_map", port: "each" }, target: { node: "n_cls" } },
  );
  return wf;
}

describe("Sprint 5 canvas rules", () => {
  it("map outputs are named flow ports with one target each", () => {
    const wf = catalog();
    expect(flowPort("next")).toBeNull();
    expect(flowPort("each")).toBe("each");
    expect(checkConnection(wf, manifests, c("n_map", "empty", "END", "in"))).toEqual({ ok: true, kind: "flow" });
    expect(checkConnection(wf, manifests, c("n_map", "each", "n_esc", "in")).reason).toMatch(/output 'each' already has a target/);
    const edge = irToFlow(wf, {}, manifests).edges.find((e) => e.id === "f_each");
    expect(edge?.sourceHandle).toBe("each");
    expect(edge?.label).toBe("each");
  });

  it("resources can feed other resources (models into a fallback middleware), never themselves", () => {
    const wf = catalog();
    expect(checkConnection(wf, manifests, c("r_fast", "model", "r_fb", "models"))).toEqual({ ok: true, kind: "wiring" });
    expect(checkConnection(wf, manifests, c("r_fb", "middleware", "r_fb", "models")).ok).toBe(false);
    expect(checkConnection(wf, manifests, c("r_fb", "middleware", "n_ag", "middleware"))).toEqual({ ok: true, kind: "wiring" });
    expect(checkConnection(wf, manifests, c("r_limit", "middleware", "n_ag", "tools")).reason).toMatch(/expects a tool \(Tool\)/);
  });

  it("new wiring into an ordered port goes last", () => {
    expect(nextOrder(catalog(), "n_ag", "middleware")).toBe(2);
    expect(nextOrder(catalog(), "n_cls", "model")).toBe(1);
    expect(nextOrder(catalog(), "n_esc", "model")).toBe(0);
  });

  it("reordering middleware re-connects only the edges whose order changes", () => {
    const wf = catalog();
    const edges = wiredEdges(wf, "n_ag", "middleware");
    expect(edges.map((e) => e.id)).toEqual(["w_l", "w_r"]);
    const cmds = reorderCommands(edges, 1, 0);
    expect(cmds.map((x) => x.op)).toEqual(["Disconnect", "Disconnect", "Connect", "Connect"]);
    const next = applyAll(wf, cmds);
    expect(wiredEdges(next, "n_ag", "middleware").map((e) => e.id)).toEqual(["w_r", "w_l"]);
    expect(reorderCommands(edges, 0, -1)).toEqual([]);
  });
});
