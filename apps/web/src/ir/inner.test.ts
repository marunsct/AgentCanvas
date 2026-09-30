import { describe, expect, it } from "vitest";
import { triage } from "../test/fixtures";
import { innerWorkflow } from "./model";

describe("innerWorkflow (subgraph drill-in)", () => {
  it("follows a path of subgraph nodes and rejects stale paths", () => {
    const wf = triage();
    const inner = { name: "Esc", state: { channels: [] }, nodes: [{ id: "x", type: "core.set", name: "x", config: {} }], edges: [] };
    wf.nodes.push({ id: "n_sub", type: "core.subgraph", name: "esc", config: { workflow: inner, inputs: [], outputs: [] } });
    expect(innerWorkflow(wf, [])?.id).toBe(wf.id);
    expect(innerWorkflow(wf, ["n_sub"])?.nodes.map((n) => n.id)).toEqual(["x"]);
    expect(innerWorkflow(wf, ["n_sub"])?.id).toBe(`${wf.id}.esc`);
    expect(innerWorkflow(wf, ["n_cls"])).toBeNull();
    expect(innerWorkflow(wf, ["missing"])).toBeNull();
  });
});
