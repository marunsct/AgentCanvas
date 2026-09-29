import { describe, expect, it } from "vitest";
import { manifests, triage } from "../test/fixtures";
import { checkConnection } from "./connect";

const c = (source: string, sourceHandle: string | null, target: string, targetHandle: string | null) =>
  ({ source, sourceHandle, target, targetHandle });

describe("checkConnection (M2-T6)", () => {
  it("refuses a Chat Model on a tools port and explains why", () => {
    const v = checkConnection(triage(), manifests, c("r_fast", "model", "n_ag", "tools"));
    expect(v.ok).toBe(false);
    expect(v.reason).toMatch(/expects Tool, but fast_model provides ChatModel/);
  });

  it("accepts a tool on a multi tools port and a model on an empty model port", () => {
    const wf = triage();
    wf.edges = wf.edges.filter((e) => e.id !== "w3" && e.id !== "w1");
    expect(checkConnection(wf, manifests, c("r_send", "tool", "n_ag", "tools"))).toEqual({ ok: true, kind: "wiring" });
    expect(checkConnection(wf, manifests, c("r_fast", "model", "n_cls", "model")).ok).toBe(true);
  });

  it("single ports accept one connection", () => {
    expect(checkConnection(triage(), manifests, c("r_fast", "model", "n_ag", "model")).reason).toMatch(/single ChatModel/);
  });

  it("flow rules: END has no outputs, START no inputs, routes have one target", () => {
    const wf = triage();
    expect(checkConnection(wf, manifests, c("END", null, "n_cls", "in")).ok).toBe(false);
    expect(checkConnection(wf, manifests, c("n_cls", "next", "START", null)).ok).toBe(false);
    expect(checkConnection(wf, manifests, c("n_rt", "route:high", "n_ag", "in")).reason).toMatch(/already has a target/);
    expect(checkConnection(wf, manifests, c("n_esc", "next", "END", "in"))).toEqual({ ok: true, kind: "flow" });
  });
});
