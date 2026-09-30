/** Contract view (FR-X-14): what callers depend on, and what a version change would break for them. */
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { ContractChange, ContractOut } from "../api/generated";

const fields = (m: Record<string, { type?: unknown }> | undefined) =>
  Object.entries(m ?? {}).map(([k, v]) => `${k}: ${String(v?.type ?? "any")}`).join(", ") || "—";

export function ContractSection({ wfId, against }: { wfId: string; against: string | null }) {
  const c = useQuery({ queryKey: ["contract", wfId], queryFn: () => apiFetch<ContractOut>(`/workflows/${wfId}/contract`) });
  const diff = useQuery({
    queryKey: ["contract-diff", wfId, against], enabled: Boolean(against),
    queryFn: () => apiFetch<ContractChange[]>(`/workflows/${wfId}/versions/${against}/contract-diff/draft`),
  });
  if (!c.data) return null;
  const breaking = (diff.data ?? []).filter((d) => d.severity === "breaking");
  return (
    <section aria-label="contract" data-testid="contract" className="small">
      <h4>Contract</h4>
      <p><b>Input</b> {fields(c.data.input as never)}</p>
      <p><b>Output</b> {fields(c.data.output as never)}</p>
      {(c.data.required_context ?? []).length > 0 && <p><b>Required context</b> {(c.data.required_context ?? []).join(", ")}</p>}
      {Object.keys(c.data.pauses ?? {}).length > 0 && (
        <p><b>May pause at</b> {Object.entries(c.data.pauses ?? {}).map(([s, k]) => `${s} (${k})`).join(", ")}</p>
      )}
      <ul>{(c.data.surfaces ?? []).map((s, i) => <li key={i}><code>{s.kind}</code> {s.detail}</li>)}</ul>
      {against && diff.data && (
        <div className={breaking.length ? "diff-box" : ""} data-testid="contract-diff">
          {breaking.length ? `⚠ ${breaking.length} breaking change(s) vs v${against}:` : `Compatible with v${against}.`}
          <ul>{(diff.data ?? []).map((d, i) => <li key={i} className={d.severity === "breaking" ? "error-text" : "muted"}>{d.message}</li>)}</ul>
        </div>
      )}
    </section>
  );
}
