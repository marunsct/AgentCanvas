import { fmt } from "./compare";

export interface ScoreLike { evaluator: string; score?: number | null; passed: boolean; comment?: string }

/** One evaluator's score: green when it passed, red when not, amber when it could not score. */
export function ScoreBadge({ s }: { s: ScoreLike }) {
  return (
    <span className={`badge ${s.score == null ? "warn" : s.passed ? "status-ok" : "err"}`} title={`${s.evaluator}: ${s.comment ?? ""}`}>
      {s.evaluator} {fmt(s.score)}
    </span>
  );
}
