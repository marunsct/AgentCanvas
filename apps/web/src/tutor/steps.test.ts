import { describe, expect, it } from "vitest";
import type { WF } from "../ir/model";
import { currentStep, STEPS } from "./steps";

const base = { id: "wf", name: "t", kind: "workflow", nodes: [], edges: [], resources: [], state: { channels: [] } } as unknown as WF;
const idle = { finished: false, interrupted: false };

describe("guided build steps", () => {
  it("advance as the workflow gains state, a step, flow, a model, a run and an approval", () => {
    let wf: WF = base;
    expect(currentStep(wf, idle)).toBe(0);
    wf = { ...wf, state: { channels: [{ name: "question" }, { name: "answer" }] } } as unknown as WF;
    expect(STEPS[currentStep(wf, idle)].id).toBe("llm");
    wf = { ...wf, nodes: [{ id: "n1", type: "core.llm", name: "llm", config: { prompt: "{{ state.question }}", output: "answer" } }] } as WF;
    expect(STEPS[currentStep(wf, idle)].id).toBe("flow");
    const flow = (id: string, a: string, b: string) => ({ id, kind: "flow", source: { node: a }, target: { node: b } });
    wf = { ...wf, edges: [flow("e1", "START", "n1"), flow("e2", "n1", "END")] } as unknown as WF;
    expect(STEPS[currentStep(wf, idle)].id).toBe("model");
    wf = {
      ...wf, resources: [{ id: "r1", type: "model.chat", name: "m", config: {} }],
      edges: [...wf.edges, { id: "w1", kind: "wiring", source: { node: "r1" }, target: { node: "n1", port: "model" } }],
    } as unknown as WF;
    expect(STEPS[currentStep(wf, idle)].id).toBe("run");
    expect(STEPS[currentStep(wf, { finished: true, interrupted: false })].id).toBe("approval");
    wf = { ...wf, nodes: [...wf.nodes, { id: "n2", type: "core.approval", name: "ok", config: {} }] } as WF;
    expect(currentStep(wf, { finished: true, interrupted: false })).toBe(STEPS.length - 1); // needs a paused run
    expect(currentStep(wf, { finished: true, interrupted: true })).toBe(STEPS.length);
  });
});
