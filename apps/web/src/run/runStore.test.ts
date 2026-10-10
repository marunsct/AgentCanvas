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

describe("model gateway and cost alerts (S26)", () => {
  it("counts cached and backup answers per node and shows cost alerts as warnings", () => {
    let s = { ...useRun.getState() };
    const step = (e: Parameters<typeof reduce>[1]) => { s = { ...s, ...reduce(s, e) }; };
    step({ type: "RUN_STARTED", threadId: "t", workflowId: "w" });
    step({ type: "CUSTOM", name: "ac.usage", value: { canvas_node_id: "n1", input_tokens: 0, output_tokens: 0, cost_usd: 0, cached: true, failed: [] } });
    step({ type: "CUSTOM", name: "ac.usage", value: { canvas_node_id: "n1", input_tokens: 10, output_tokens: 5, cost_usd: 0.001 } });
    step({ type: "CUSTOM", name: "ac.usage", value: { canvas_node_id: "n1", input_tokens: 0, output_tokens: 0, cost_usd: 0, cached: false,
      failed: [{ model: "openai:gpt-5-mini", error: "down" }] } });
    expect(s.metrics.n1).toMatchObject({ tokens: 15, cached: 1, backups: 1 });
    expect(formatMetrics(s.metrics.n1)).toBe("15 tok · $0.0010 · 1 cached · 1 by backup model");
    step({ type: "CUSTOM", name: "ac.cost_alert", value: { message: "g used 80% of its day budget: 0.8 of 1 USD" } });
    expect(s.warnings).toEqual(["g used 80% of its day budget: 0.8 of 1 USD"]);
  });
});

describe("rubric rounds", () => {
  it("shows each grading round live and keeps its verdict", () => {
    const s = useRun.getState();
    s.reset();
    const step = (ev: Parameters<typeof s.apply>[0]) => useRun.getState().apply(ev);
    step({ type: "CUSTOM", name: "ac.rubric", canvas_node_id: "agent", value: { phase: "start", iteration: 0 } });
    expect(useRun.getState().rubric).toEqual([{ node: "agent", iteration: 0, result: null, explanation: "", gaps: [] }]);
    step({
      type: "CUSTOM", name: "ac.rubric", canvas_node_id: "agent",
      value: { phase: "end", iteration: 0, result: "needs_revision", explanation: "no city", criteria: [{ name: "city", passed: false, gap: "name it" }] },
    });
    step({ type: "CUSTOM", name: "ac.rubric", canvas_node_id: "agent", value: { phase: "end", iteration: 1, result: "satisfied", criteria: [] } });
    expect(useRun.getState().rubric.map((r) => [r.iteration, r.result, r.gaps])).toEqual([[0, "needs_revision", ["city: name it"]], [1, "satisfied", []]]);
  });
});
