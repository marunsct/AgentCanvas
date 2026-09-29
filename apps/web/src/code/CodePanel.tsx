/** Generated code view with source-map highlighting of the selected node (first step of FR-X-01). */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { ApiError, apiFetch } from "../api/client";
import { useEditor } from "../editor/store";

export function CodePanel() {
  const workflowId = useEditor((s) => s.workflowId);
  const irHash = useEditor((s) => s.irHash);
  const selected = useEditor((s) => s.selected);
  const { data, error, isFetching } = useQuery({
    queryKey: ["code", workflowId, irHash],
    queryFn: () => apiFetch<string>(`/workflows/${workflowId}/code`),
    enabled: Boolean(workflowId),
    retry: false,
  });
  const lines = useMemo(() => (data ?? "").split("\n"), [data]);
  const [start, end] = useMemo(() => {
    if (!selected) return [-1, -1];
    const s = lines.findIndex((l) => l.includes(`@canvas-node ${selected} `) || l.includes(`@canvas-resource ${selected} `));
    if (s < 0) return [-1, -1];
    const e = lines.findIndex((l, i) => i > s && /@canvas-(node|resource)|── flow edges ──/.test(l));
    return [s, e < 0 ? lines.length : e];
  }, [lines, selected]);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector(".hl")?.scrollIntoView({ block: "nearest" });
  }, [start]);

  if (error) {
    const errs = error instanceof ApiError ? (error.problem.errors as { code: string; message: string }[] | undefined) : undefined;
    return (
      <div className="panel-body">
        <p className="error-text">Code is generated once the workflow has no errors.</p>
        <ul className="diag-list">{errs?.map((e, i) => <li key={i} className="error"><b>{e.code}</b> {e.message}</li>)}</ul>
      </div>
    );
  }
  return (
    <div className="code-view" ref={ref} aria-busy={isFetching}>
      {lines.map((l, i) => (
        <div key={i} className={i >= start && i < end ? "hl" : undefined}>
          <span className="ln">{i + 1}</span>{l || " "}
        </div>
      ))}
    </div>
  );
}
