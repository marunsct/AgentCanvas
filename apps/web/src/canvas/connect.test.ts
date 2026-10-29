import { describe, expect, it } from "vitest";
import { applyAll } from "../ir/commands";
import type { WF } from "../ir/model";
import { manifests, triage } from "../test/fixtures";
import { checkConnection, routeEdgeChanges } from "./connect";

const c = (source: string, sourceHandle: string | null, target: string, targetHandle: string | null) =>
  ({ source, sourceHandle, target, targetHandle });

describe("checkConnection (M2-T6)", () => {
  it("refuses a Chat Model on a tools port and explains why", () => {
    const v = checkConnection(triage(), manifests, c("r_fast", "model", "n_ag", "tools"));
    expect(v.ok).toBe(false);
    expect(v.reason).toMatch(/expects a tool \(Tool\), but fast_model is an AI model \(ChatModel\)/);
  });

  it("accepts a tool on a multi tools port and a model on an empty model port", () => {
    const wf = triage();
    wf.edges = wf.edges.filter((e) => e.id !== "w3" && e.id !== "w1");
    expect(checkConnection(wf, manifests, c("r_send", "tool", "n_ag", "tools"))).toEqual({ ok: true, kind: "wiring" });
    expect(checkConnection(wf, manifests, c("r_fast", "model", "n_cls", "model")).ok).toBe(true);
  });

  it("single ports accept one connection", () => {
    expect(checkConnection(triage(), manifests, c("r_fast", "model", "n_ag", "model")).reason).toMatch(/single AI model \(ChatModel\)/);
  });

  it("flow rules: END has no outputs, START no inputs, routes have one target", () => {
    const wf = triage();
    expect(checkConnection(wf, manifests, c("END", null, "n_cls", "in")).ok).toBe(false);
    expect(checkConnection(wf, manifests, c("n_cls", "next", "START", null)).ok).toBe(false);
    expect(checkConnection(wf, manifests, c("n_rt", "route:high", "n_ag", "in")).reason).toMatch(/already has a target/);
    expect(checkConnection(wf, manifests, c("n_esc", "next", "END", "in"))).toEqual({ ok: true, kind: "flow" });
  });
});

describe("routeEdgeChanges (W-6)", () => {
  // the triage router: cases [{route: "high"}], default "normal"; e3 leaves route:high, e4 route:normal
  const router = (wf: WF) => wf.nodes.find((n) => n.id === "n_rt")!;
  const when = 'state.urgency == "high"';

  it("removes the arrow of a removed path in the same edit", () => {
    const wf = triage();
    expect(routeEdgeChanges(wf, router(wf), { cases: [], default: "normal" })).toEqual([{ op: "Disconnect", id: "e3" }]);
  });

  it("moves the arrow of a renamed path to the new name, in its place", () => {
    const wf = triage();
    const cmds = routeEdgeChanges(wf, router(wf), { cases: [{ when, route: "urgent" }], default: "rest" });
    const e3 = wf.edges.find((e) => e.id === "e3")!, e4 = wf.edges.find((e) => e.id === "e4")!;
    expect(cmds).toEqual([
      { op: "Disconnect", id: "e3" }, { op: "Connect", edge: { ...e3, source: { node: "n_rt", port: "route:urgent" } }, index: 2 },
      { op: "Disconnect", id: "e4" }, { op: "Connect", edge: { ...e4, source: { node: "n_rt", port: "route:rest" } }, index: 3 },
    ]);
    expect(applyAll(wf, cmds).edges.map((e) => e.id)).toEqual(wf.edges.map((e) => e.id));
  });

  it("keeps the arrow while a path name is cleared to be retyped, and leaves other paths alone", () => {
    const wf = triage();
    expect(routeEdgeChanges(wf, router(wf), { cases: [{ when, route: "" }], default: "normal" })).toEqual([]);
    expect(routeEdgeChanges(wf, router(wf), { cases: [{ when, route: "high" }, { when: "true", route: "x" }], default: "normal" })).toEqual([]);
  });
});
