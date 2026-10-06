/** Pure helpers for the Evaluation page: the results grid and the comparison of two experiments. */
import type { ExperimentOut, ExperimentResultOut } from "../api/generated";

export interface GridRow {
  exampleId: string;
  position: number;
  cells: (ExperimentResultOut | undefined)[]; // one per target
}

/** Examples x targets, in dataset order. */
export function grid(results: ExperimentResultOut[], targets: number): GridRow[] {
  const rows = new Map<string, GridRow>();
  for (const r of results) {
    const row = rows.get(r.example_id) ?? { exampleId: r.example_id, position: r.position, cells: Array(targets).fill(undefined) };
    row.cells[r.target] = r;
    rows.set(r.example_id, row);
  }
  return [...rows.values()].sort((a, b) => a.position - b.position);
}

/** Whether a finished result passed every evaluator (undefined while it runs). */
export function passed(r: ExperimentResultOut | undefined): boolean | undefined {
  if (!r || r.status !== "done") return undefined;
  return r.scores.every((s) => s.passed);
}

export interface EvaluatorDelta { evaluator: string; before: number | null; after: number | null; delta: number | null }
export interface Comparison {
  evaluators: EvaluatorDelta[];
  passRate: { before: number | null; after: number | null; delta: number | null };
  improved: string[]; // example ids that failed in the baseline and pass now
  regressed: string[];
}

const diff = (a: number | null | undefined, b: number | null | undefined) =>
  a == null || b == null ? null : Math.round((b - a) * 10000) / 10000;

/** `after` against the baseline `before`, comparing one target of each (the first by default). */
export function compare(
  before: ExperimentOut, beforeResults: ExperimentResultOut[],
  after: ExperimentOut, afterResults: ExperimentResultOut[],
  targets: [number, number] = [0, 0],
): Comparison {
  const a = before.summary[targets[0]];
  const b = after.summary[targets[1]];
  const names = [...new Set([...(a?.evaluators ?? []), ...(b?.evaluators ?? [])].map((e) => e.evaluator))];
  const mean = (s: typeof a, name: string) => s?.evaluators.find((e) => e.evaluator === name)?.mean ?? null;
  const outcome = (rs: ExperimentResultOut[], t: number) =>
    new Map(rs.filter((r) => r.target === t).map((r) => [r.example_id, passed(r)]));
  const was = outcome(beforeResults, targets[0]);
  const now = outcome(afterResults, targets[1]);
  const improved: string[] = [];
  const regressed: string[] = [];
  for (const [id, p] of now) {
    const q = was.get(id);
    if (p === undefined || q === undefined) continue;
    if (p && !q) improved.push(id);
    if (!p && q) regressed.push(id);
  }
  return {
    evaluators: names.map((n) => ({ evaluator: n, before: mean(a, n), after: mean(b, n), delta: diff(mean(a, n), mean(b, n)) })),
    passRate: { before: a?.pass_rate ?? null, after: b?.pass_rate ?? null, delta: diff(a?.pass_rate, b?.pass_rate) },
    improved,
    regressed,
  };
}

/** "0.83" / "—" */
export const fmt = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));
export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** Starting configs for each evaluator kind. */
export const KIND_CONFIG: Record<string, unknown> = {
  exact: {},
  regex: { channel: "out", pattern: "" },
  json_schema: { channel: "out", schema: { type: "object" } },
  llm_judge: { model: "anthropic:claude-haiku-4-5", rubric: "Is the answer correct and polite?" },
  trajectory: { mode: "strict" },
  code: { function: "scripts.evals:check" },
  composite: { evaluators: [], mode: "weighted", weights: {} },
  pairwise: { model: "anthropic:claude-haiku-4-5", rubric: "Which answer is more helpful and correct?" },
};
