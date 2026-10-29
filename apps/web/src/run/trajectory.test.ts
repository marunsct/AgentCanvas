import { describe, expect, it } from "vitest";
import type { TrajectoryNode } from "../api/generated";
import { bar, rows, stepsOf, took } from "./trajectory";

const tool: TrajectoryNode = { id: "r:4", kind: "tool", label: "Tool search", start_ms: 30, duration_ms: null };
const helper: TrajectoryNode = { id: "r:3", kind: "agent", label: "Helper agent researcher", start_ms: 20, children: [tool] };
const step: TrajectoryNode = { id: "r:2", kind: "step", label: "Planner", canvas_node_id: "n_plan", start_ms: 10, duration_ms: 50, children: [helper] };
const run: TrajectoryNode = { id: "r:1", kind: "run", label: "Run 1", start_ms: 0, duration_ms: 100, children: [step] };

describe("trajectory rows", () => {
  it("opens runs, frames and helpers by default and steps when clicked", () => {
    expect(rows([run], new Set()).map((r) => [r.node.id, r.depth, r.open])).toEqual([["r:1", 0, true], ["r:2", 1, false]]);
    expect(rows([run], new Set(["r:2"])).map((r) => r.node.id)).toEqual(["r:1", "r:2", "r:3", "r:4"]);
    expect(rows([run], new Set(["r:1"])).map((r) => r.node.id)).toEqual(["r:1"]); // a run closed by hand
  });

  it("places rows on the run's time bar", () => {
    expect(bar(step, run)).toEqual({ left: 10, width: 50 });
    expect(bar(tool, run)).toEqual({ left: 30, width: 1 }); // a point in time still shows
    expect(bar(step, { ...run, duration_ms: null })).toBeNull();
  });

  it("formats durations and finds the steps a run went through", () => {
    expect([took(850), took(1234), took(180_000), took(null)]).toEqual(["850 ms", "1.2 s", "3 min", ""]);
    expect(stepsOf(run)).toEqual(["n_plan"]);
  });
});
