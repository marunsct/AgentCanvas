import { describe, expect, it } from "vitest";
import type { ExperimentOut, ExperimentResultOut } from "../api/generated";
import { compare, grid, passed } from "./compare";

const result = (example_id: string, target: number, ok: boolean, position = 0): ExperimentResultOut => ({
  id: `exr_${example_id}_${target}`, target, example_id, position, status: "done", run_id: null, thread_id: null,
  run_status: "success", error: null, output: {}, trajectory: [], tokens_in: 0, tokens_out: 0, cost_usd: 0, latency_ms: 1,
  scores: [{ evaluator: "exact", score: ok ? 1 : 0, passed: ok, comment: "" }],
});

const experiment = (mean: number, pass_rate: number): ExperimentOut => ({
  id: "exp", workflow_id: "wf", name: "e", dataset_id: "ds", dataset_version: 1, split: null, targets: [],
  evaluators: ["exact"], concurrency: 1, status: "done", error: null, created_at: "", ended_at: null,
  summary: [{
    target: 0, label: "draft", examples: 2, completed: 2, errors: 0, pass_rate, latency_p50_ms: 1, latency_p95_ms: 1,
    tokens: 0, cost_usd: 0, evaluators: [{ evaluator: "exact", mean, pass_rate, scored: 2 }],
  }],
});

describe("evaluation helpers", () => {
  it("lays results out as examples x targets in dataset order", () => {
    const rows = grid([result("b", 1, true, 1), result("a", 0, false, 0), result("b", 0, true, 1)], 2);
    expect(rows.map((r) => r.exampleId)).toEqual(["a", "b"]);
    expect(rows[0].cells[1]).toBeUndefined();
    expect(passed(rows[1].cells[1])).toBe(true);
    expect(passed({ ...result("c", 0, true), status: "running" })).toBeUndefined();
  });

  it("compares two experiments per evaluator and per example", () => {
    const c = compare(
      experiment(0.5, 0.5), [result("a", 0, true), result("b", 0, false)],
      experiment(0.5, 0.5), [result("a", 0, false), result("b", 0, true)],
    );
    expect(c.evaluators).toEqual([{ evaluator: "exact", before: 0.5, after: 0.5, delta: 0 }]);
    expect(c.improved).toEqual(["b"]);
    expect(c.regressed).toEqual(["a"]);
    expect(compare(experiment(0.25, 0), [], experiment(0.75, 1), []).passRate.delta).toBe(1);
  });
});
