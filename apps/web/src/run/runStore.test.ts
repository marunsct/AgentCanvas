import { describe, expect, it } from "vitest";
import { reduce, useRun } from "./runStore";

describe("run reducer", () => {
  it("lights nodes up, captures interrupts and the final output", () => {
    let st = useRun.getState();
    const step = (ev: Parameters<typeof reduce>[1]) => { st = { ...st, ...reduce(st, ev) }; };
    step({ type: "RUN_STARTED", threadId: "t1", workflowId: "w", runId: "r1" });
    step({ type: "STEP_STARTED", stepName: "classify", canvas_node_id: "n_cls" });
    expect(st.status.n_cls).toBe("running");
    step({ type: "TEXT_MESSAGE_CONTENT", canvas_node_id: "n_cls", delta: "lo" });
    step({ type: "TEXT_MESSAGE_CONTENT", canvas_node_id: "n_cls", delta: "w" });
    expect(st.output.n_cls).toBe("low");
    step({ type: "STEP_FINISHED", stepName: "classify", canvas_node_id: "n_cls", status: "ok" });
    step({ type: "CUSTOM", name: "ac.interrupt", value: { id: "i1", kind: "approval", canvas_node_id: "n_ok", payload: {} } });
    expect(st.interrupt?.id).toBe("i1");
    step({ type: "RUN_FINISHED", threadId: "t1", outcome: "interrupt", output: { urgency: "low" } });
    expect(st.running).toBe(false);
    expect(st.finalOutput).toEqual({ urgency: "low" });
    expect(st.runId).toBe("r1");
  });

  it("records errors and warnings", () => {
    let st = useRun.getState();
    st = { ...st, ...reduce(st, { type: "CUSTOM", name: "ac.warning", value: { message: "tokens off" } }) };
    st = { ...st, ...reduce(st, { type: "RUN_ERROR", message: "boom" }) };
    expect(st.warnings).toEqual(["tokens off"]);
    expect(st.error).toBe("boom");
  });
});
