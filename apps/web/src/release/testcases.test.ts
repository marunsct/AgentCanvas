import { describe, expect, it } from "vitest";
import type { WF } from "../ir/model";
import { testCaseFromRun } from "./testcases";

const wf = {
  id: "wf", name: "t", nodes: [], edges: [], resources: [],
  state: { channels: [{ name: "messages", reducer: "add_messages" }, { name: "summary" }] },
} as unknown as WF;
const base = { running: false, input: { ticket: "x" }, route: ["n_a", "n_b"], interrupts: [], error: null };

describe("testCaseFromRun", () => {
  it("captures status, route and non-chat outputs", () => {
    const tc = testCaseFromRun(wf, { ...base, finalOutput: { summary: "S", messages: [{ id: 1 }] } }, { mocks: { m: ["hi"] } }, "n");
    expect(tc).toEqual({
      name: "n", input: { ticket: "x" }, mocks: { m: ["hi"] },
      expect: { status: "success", channels: { summary: "S" }, route: ["n_a", "n_b"] },
    });
  });
  it("marks interrupted and error runs; refuses runs it cannot replay", () => {
    const intr = { id: "i", kind: "approval" as const, canvas_node_id: null, payload: null };
    expect(testCaseFromRun(wf, { ...base, finalOutput: {}, interrupts: [intr] }, {}, "n")?.expect?.status).toBe("interrupted");
    expect(testCaseFromRun(wf, { ...base, finalOutput: null, error: "boom" }, {}, "n")?.expect?.status).toBe("error");
    expect(testCaseFromRun(wf, { ...base, input: null, finalOutput: {} }, {}, "n")).toBeNull(); // continued thread
    expect(testCaseFromRun(wf, { ...base, running: true, finalOutput: {} }, {}, "n")).toBeNull();
  });
});
