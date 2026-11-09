/** Runs page helpers: turning chosen conversations into dataset examples. */
import type { RunOut } from "../api/generated";

/** Only new runs that finished or paused keep a recording the dataset can take (the server's `REPLAYABLE`). */
const REPLAYABLE = ["success", "interrupted"];

/** The run of a conversation to add as an example: its last one that has a recording, whose output is the
 * conversation's answer. `undefined` when none of its runs qualifies (an error, a cancelled or an old run). */
export function exampleRun(runs: Pick<RunOut, "id" | "status">[]): string | undefined {
  return [...runs].reverse().find((r) => REPLAYABLE.includes(r.status))?.id;
}

/** What to say about a selection, or null when it can go to a dataset: examples belong to one workflow's datasets. */
export function selectionProblem(workflowIds: string[]): string | null {
  const distinct = new Set(workflowIds);
  if (distinct.size === 0) return "Pick conversations first.";
  return distinct.size > 1 ? "These belong to different workflows: pick the conversations of one workflow." : null;
}

/** The "Started" choices on the Runs page. */
export const WHEN = [
  { id: "", label: "Any time", hours: 0 },
  { id: "24h", label: "Last 24 hours", hours: 24 },
  { id: "7d", label: "Last 7 days", hours: 24 * 7 },
  { id: "30d", label: "Last 30 days", hours: 24 * 30 },
] as const;
export type When = (typeof WHEN)[number]["id"];

/** The `since` the API takes for a choice of "Started" (an ISO time), or undefined for any time. */
export function sinceFor(when: When, now: Date = new Date()): string | undefined {
  const hours = WHEN.find((w) => w.id === when)?.hours;
  return hours ? new Date(now.getTime() - hours * 3_600_000).toISOString() : undefined;
}
