/** Saved test cases (M6-T9): list, run against the draft, see which assertion failed. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { CaseResult, TestCaseOut, TestRunOut } from "../api/generated";
import { useEditor } from "../editor/store";

export function TestsPanel() {
  const wf = useEditor((s) => s.wf);
  const qc = useQueryClient();
  const key = ["test-cases", wf?.id];
  const cases = useQuery({ queryKey: key, enabled: Boolean(wf), queryFn: () => apiFetch<TestCaseOut[]>(`/workflows/${wf!.id}/test-cases`) });
  const run = useMutation({
    mutationFn: async () => {
      await useEditor.getState().flush();
      return apiFetch<TestRunOut>(`/workflows/${wf!.id}/test-cases/run`, { method: "POST", json: {} });
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: key }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/workflows/${wf!.id}/test-cases/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: key }),
  });
  if (!wf) return null;
  const list = cases.data ?? [];
  return (
    <div className="panel-body tests-panel">
      <h3>Test cases</h3>
      <div className="row gap">
        <button type="button" className="primary" disabled={list.length === 0 || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "Running…" : "▶ Run tests"}
        </button>
        {run.data && (
          <span className={`badge ${run.data.failed ? "err" : "ok"}`} data-testid="test-summary">
            {run.data.passed} passed · {run.data.failed} failed
          </span>
        )}
      </div>
      {run.error && <p className="error-text">{(run.error as Error).message}</p>}
      {list.length === 0 && <p className="muted">Run the workflow, then “Save as test case” in the Run tab.</p>}
      <ul className="case-list">
        {list.map((c) => <CaseRow key={c.id} c={c} onDelete={() => remove.mutate(c.id)} />)}
      </ul>
    </div>
  );
}

function CaseRow({ c, onDelete }: { c: TestCaseOut; onDelete: () => void }) {
  const r = c.last_result as CaseResult | null | undefined;
  return (
    <li data-testid={`case-${c.name}`}>
      <div className="row gap">
        <span className={`badge ${r ? (r.passed ? "ok" : "err") : ""}`}>{r ? (r.passed ? "pass" : "fail") : "not run"}</span>
        <b>{c.name}</b>
        <span className="spacer" />
        <button type="button" className="link small" aria-label={`delete test ${c.name}`} onClick={onDelete}>✕</button>
      </div>
      {r && !r.passed && <ul className="failures">{(r.failures ?? []).map((f, i) => <li key={i} className="error-text small">{f}</li>)}</ul>}
    </li>
  );
}
