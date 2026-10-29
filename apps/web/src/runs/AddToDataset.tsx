/** Runs page: the chosen conversations become examples of one of their workflow's datasets (the last finished run of
 * each: its input, and its answer as the reference). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import type { DatasetOut, RunOut, ThreadOut } from "../api/generated";
import { toast } from "../ui/dialog";
import { exampleRun, selectionProblem } from "./runs";

export function AddToDataset({ threads, onDone }: { threads: ThreadOut[]; onDone: () => void }) {
  const qc = useQueryClient();
  const problem = selectionProblem(threads.map((t) => t.workflow_id));
  const workflowId = problem ? null : threads[0].workflow_id;
  const datasets = useQuery({
    queryKey: ["datasets", workflowId],
    queryFn: () => apiFetch<DatasetOut[]>(`/workflows/${workflowId}/datasets`),
    enabled: workflowId !== null,
  });
  const [chosen, setChosen] = useState("");
  const dataset = chosen || datasets.data?.[0]?.id || "";
  const add = useMutation({
    mutationFn: async () => {
      const lists = await Promise.all(threads.map((t) => apiFetch<RunOut[]>(`/threads/${t.id}/runs`)));
      const ids = lists.map(exampleRun).filter((id): id is string => id !== undefined);
      if (ids.length === 0) throw new Error("None of these conversations has a finished run with a recorded input.");
      await apiFetch(`/datasets/${dataset}/examples/from-runs`, { method: "POST", json: { run_ids: ids } });
      return { added: ids.length, skipped: threads.length - ids.length };
    },
    onSuccess: ({ added, skipped }) => {
      toast(`Added ${added} example${added === 1 ? "" : "s"}${skipped ? `; ${skipped} had no finished run to add` : ""}`);
      void qc.invalidateQueries({ queryKey: ["datasets"] });
      void qc.invalidateQueries({ queryKey: ["dataset", dataset] });
      onDone();
    },
  });
  return (
    <div className="row gap wrap add-to-dataset" role="region" aria-label="add to a dataset">
      <strong>{threads.length} selected</strong>
      {problem ? <span className="muted small">{problem}</span> : datasets.data?.length === 0 ? (
        <span className="muted small">This workflow has no dataset yet: <Link to={`/workflows/${workflowId}/evaluation`}>create one in Evaluation</Link>.</span>
      ) : (
        <>
          <select aria-label="dataset" value={dataset} onChange={(e) => setChosen(e.target.value)}>
            {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.examples} examples)</option>)}
          </select>
          <button type="button" disabled={!dataset || add.isPending} onClick={() => add.mutate()}>
            {add.isPending ? "Adding…" : "Add these runs as examples"}
          </button>
        </>
      )}
      {add.error && <span className="error-text small" role="alert">Could not add: {(add.error as Error).message}</span>}
    </div>
  );
}
