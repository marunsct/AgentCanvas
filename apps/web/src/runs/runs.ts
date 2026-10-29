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
