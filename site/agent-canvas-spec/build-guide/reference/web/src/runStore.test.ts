import { describe, expect, it } from "vitest";
import { reduce, useRun } from "./runStore";
import { irToFlow } from "./irToFlow";
import type { Workflow } from "./ir";
import triage from "../../backend/examples/support_triage.ir.json";

describe("run reducer", () => {
  it("lights nodes up and captures interrupts", () => {
    const s = useRun.getState();
    let st = { ...s, ...reduce(s, { type: "RUN_STARTED", threadId: "t1", workflowId: "w" }) };
    st = { ...st, ...reduce(st, { type: "STEP_STARTED", stepName: "classify", canvas_node_id: "n_cls" }) };
    expect(st.status.n_cls).toBe("running");
    st = { ...st, ...reduce(st, { type: "CUSTOM", name: "ac.interrupt", value: { id: "i1", payload: { x: 1 } } }) };
    expect(st.interrupt?.id).toBe("i1");
    st = { ...st, ...reduce(st, { type: "RUN_FINISHED", threadId: "t1", outcome: "interrupt" }) };
    expect(st.running).toBe(false);
  });
});

describe("irToFlow", () => {
  it("creates one canvas node per IR node plus START/END and resources", () => {
    const { nodes, edges } = irToFlow(triage as Workflow);
    expect(nodes).toHaveLength(5 + 2 + 3);
    expect(edges.find((e) => e.id === "e3")?.label).toBe("high");
  });
});
