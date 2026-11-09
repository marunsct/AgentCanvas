/** Pure helpers for the Evaluation page: the results grid and the comparison of two experiments. */
import type { ExperimentOut, ExperimentResultOut } from "../api/generated";

export interface GridRow {
  key: string; // the example, and which run of it when an experiment repeats its examples
  exampleId: string;
  position: number;
  repetition: number;
  cells: (ExperimentResultOut | undefined)[]; // one per target
}

/** Examples (each run of an example, when repeated) x targets, in dataset order. */
export function grid(results: ExperimentResultOut[], targets: number): GridRow[] {
  const rows = new Map<string, GridRow>();
  for (const r of results) {
    const repetition = r.repetition ?? 0;
    const key = `${r.example_id}:${repetition}`;
    const row = rows.get(key) ?? { key, exampleId: r.example_id, position: r.position, repetition, cells: Array(targets).fill(undefined) };
    row.cells[r.target] = r;
    rows.set(key, row);
  }
  return [...rows.values()].sort((a, b) => a.position - b.position || a.repetition - b.repetition);
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
  // an example passes when every run of it did (an experiment may run each example several times); unknown while any runs
  const outcome = (rs: ExperimentResultOut[], t: number) => {
    const out = new Map<string, boolean | undefined>();
    for (const r of rs.filter((x) => x.target === t)) {
      const p = passed(r);
      const seen = out.has(r.example_id) ? out.get(r.example_id) : true;
      out.set(r.example_id, seen === undefined || p === undefined ? undefined : seen && p);
    }
    return out;
  };
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

/** Convert and compare (S25): one target per installed framework the experiment does not run on yet. */
export function frameworksToCompare<T extends { framework: string }>(
  frameworks: { id: string }[], own: string, targets: T[], blank: () => Omit<T, "framework">,
): T[] {
  const covered = new Set(targets.map((t) => t.framework || own));
  return frameworks.filter((f) => !covered.has(f.id)).map((f) => ({ ...blank(), framework: f.id }) as T);
}
