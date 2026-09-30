import { describe, expect, it } from "vitest";
import { formatMetrics, reduce, useRun } from "./runStore";

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

describe("usage and latency (M4-T11)", () => {
  it("adds up tokens, cost and time per node, and keeps run totals", () => {
    let s = { ...useRun.getState() };
    const step = (e: Parameters<typeof reduce>[1]) => { s = { ...s, ...reduce(s, e) }; };
    step({ type: "RUN_STARTED", threadId: "t", workflowId: "w" });
    step({ type: "CUSTOM", name: "ac.usage", value: { canvas_node_id: "n1", input_tokens: 900, output_tokens: 300, cost_usd: 0.002 } });
    step({ type: "CUSTOM", name: "ac.usage", value: { canvas_node_id: "n1", input_tokens: 10, output_tokens: 5, cost_usd: null } });
    step({ type: "STEP_FINISHED", stepName: "a", canvas_node_id: "n1", status: "ok", duration_ms: 850 });
    step({ type: "STEP_FINISHED", stepName: "a", canvas_node_id: "n1", status: "ok", duration_ms: 400 });
    expect(s.metrics.n1).toEqual({ tokens: 1215, cost: 0.002, priced: false, ms: 1250 });
    expect(formatMetrics(s.metrics.n1)).toBe("1.2k tok · $0.0020+ · 1.3 s");
    step({ type: "RUN_FINISHED", threadId: "t", outcome: "success", duration_ms: 2000,
      usage: { input_tokens: 910, output_tokens: 305, cost_usd: 0.002, priced: false } });
    expect(s.usage?.priced).toBe(false);
    expect(formatMetrics({ tokens: 12, cost: 0, priced: false, ms: 0 })).toBe("12 tok");
  });
});
