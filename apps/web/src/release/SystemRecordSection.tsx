/** A deployment's AI-system record (E-13): what it is for, who owns it, its risk class and the data it handles,
 * filled in here; the evidence (models, tools, approvals, evaluations, who published it) is read by the server.
 * Anyone may read it and download it for an auditor; builders fill it in. Notes are reminders, never errors. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import type { DeploymentOut, MemberOut } from "../api/generated";
import { can } from "../api/session";

// narrow local types until the generated API types are regenerated (GET/PUT /deployments/{id}/system-record)
export type RiskClass = "minimal" | "limited" | "high";
export interface RecordFields {
  purpose: string;
  owner_id: string | null;
  risk_class: RiskClass | null;
  risk_rationale: string;
  data_categories: string[];
  end_user_facing: boolean | null;
  review_date: string | null;
}
export interface SystemRecordOut {
  record: RecordFields & { owner_email: string | null; owner_member: boolean; updated_by_email: string | null; updated_at: string | null };
  version: { semver: string };
  models: { model: string }[];
  subprocessors: { name: string }[];
  tools: { name: string }[];
  approvals: { step: string }[];
  ungated: string[];
  evaluation: { release_gate: unknown; experiment: unknown; red_team: unknown; online: unknown[]; test_cases: number };
  history: unknown[];
  notes: string[];
}

const RISKS: [RiskClass, string][] = [
  ["minimal", "Minimal: no particular risk to people"],
  ["limited", "Limited: people must be told they deal with an AI"],
  ["high", "High: decisions about people's work, credit, education, essential services or safety"],
];
const CATEGORIES = [
  "public information", "internal business data", "customer contact details", "personal data",
  "special-category personal data", "children's data", "financial data", "health data", "credentials or secrets",
];
const EMPTY: RecordFields = {
  purpose: "", owner_id: null, risk_class: null, risk_rationale: "", data_categories: [], end_user_facing: null, review_date: null,
};

function evidence(r: SystemRecordOut): string {
  const ev = r.evaluation;
  const kinds = [ev.release_gate && "a release gate", ev.experiment && "an experiment", ev.red_team && "a red-team run",
    ev.online.length > 0 && "online evaluators"].filter(Boolean);
  return kinds.length ? kinds.join(", ") : "none yet";
}

export function SystemRecordSection({ deployment }: { deployment: DeploymentOut }) {
  const qc = useQueryClient();
  const key = ["system-record", deployment.id];
  const url = `/deployments/${deployment.id}/system-record`;
  const rec = useQuery({ queryKey: key, queryFn: () => apiFetch<SystemRecordOut>(url) });
  const members = useQuery({ queryKey: ["members"], queryFn: () => apiFetch<MemberOut[]>("/org/members"), enabled: can("builder") });
  const [form, setForm] = useState<RecordFields>(EMPTY);
  const [other, setOther] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const seeded = useRef<string | null>(null); // the deployment the form was filled from: a refetch must not wipe typing
  const seed = (r: SystemRecordOut) => {
    const { purpose, owner_id, risk_class, risk_rationale, data_categories, end_user_facing, review_date } = r.record;
    setForm({ purpose, owner_id, risk_class, risk_rationale, data_categories, end_user_facing, review_date });
    setOther(data_categories.filter((c) => !CATEGORIES.includes(c)).join(", "));
  };
  useEffect(() => {
    if (!rec.data || seeded.current === deployment.id) return;
    seeded.current = deployment.id;
    seed(rec.data);
  }, [rec.data, deployment.id]);
  const save = useMutation({
    mutationFn: (body: RecordFields) => apiFetch<SystemRecordOut>(url, { method: "PUT", json: body }),
    onSuccess: (out) => {
      qc.setQueryData(key, out);
      seed(out);
      setStatus("Saved.");
      void qc.invalidateQueries({ queryKey: ["share", deployment.id] }); // the AI notice's default wording may change
    },
  });
  const download = useMutation({
    mutationFn: (format: "markdown" | "html") =>
      apiDownload(`${url}/export?format=${format}`, `system-record.${format === "html" ? "html" : "md"}`),
  });
  const set = (patch: Partial<RecordFields>) => { setForm((f) => ({ ...f, ...patch })); setStatus(null); };
  const ticked = (c: string) => form.data_categories.includes(c);
  const tick = (c: string, on: boolean) =>
    set({ data_categories: on ? [...form.data_categories, c] : form.data_categories.filter((x) => x !== c) });
  const submit = () => {
    const extra = other.split(",").map((s) => s.trim()).filter(Boolean);
    const categories = [...form.data_categories.filter((c) => CATEGORIES.includes(c)), ...extra];
    save.mutate({ ...form, purpose: form.purpose.trim(), risk_rationale: form.risk_rationale.trim(), data_categories: [...new Set(categories)] });
  };
  const id = `record-${deployment.id}`;
  const data = rec.data;
  const editable = can("builder");
  const owners = (members.data ?? []).filter((m) => m.accepted !== false && !m.deactivated);
  const needsReason = form.risk_class !== null && !form.risk_rationale.trim();
  const error = (rec.error ?? save.error ?? download.error) as Error | null;
  return (
    <details className="connect share-box small" data-testid={`record-${deployment.environment}`}>
      <summary>System record{data?.record.risk_class ? ` (${data.record.risk_class} risk)` : ""}{data && data.notes.length > 0 ? ` · ${data.notes.length} note${data.notes.length === 1 ? "" : "s"}` : ""}</summary>
      <p>
        What this AI system is for, who answers for it and how risky it is, with the evidence the server keeps: the
        record an auditor or your AI inventory asks for. Download it to hand on.
      </p>
      {error && <p className="error-text" role="alert">{error.message}</p>}
      {data && (
        <>
          {data.notes.length > 0 && (
            <ul className="record-notes" aria-label="notes" data-testid="record-notes">
              {data.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          )}
          <p className="muted" data-testid="record-evidence">
            v{data.version.semver}: {data.models.length} model{data.models.length === 1 ? "" : "s"}, {data.subprocessors.length} outside
            service{data.subprocessors.length === 1 ? "" : "s"}, {data.tools.length} tool{data.tools.length === 1 ? "" : "s"}, {data.approvals.length} approval
            step{data.approvals.length === 1 ? "" : "s"}{data.ungated.length > 0 ? ` (${data.ungated.length} acting without one)` : ""}; evaluation evidence: {evidence(data)}.
          </p>
          <fieldset disabled={!editable || save.isPending} className="record-form">
            <label htmlFor={`${id}-purpose`}>Purpose</label>
            <textarea id={`${id}-purpose`} value={form.purpose} maxLength={2000}
                      placeholder="What it is for, and what it must not be used for"
                      onChange={(e) => set({ purpose: e.target.value })} />
            <label htmlFor={`${id}-owner`}>Owner</label>
            <select id={`${id}-owner`} value={form.owner_id ?? ""} onChange={(e) => set({ owner_id: e.target.value || null })}>
              <option value="">Nobody named</option>
              {form.owner_id && !owners.some((m) => m.user_id === form.owner_id) && (
                <option value={form.owner_id}>{data.record.owner_email ?? `${form.owner_id} (no longer a member)`}</option>
              )}
              {owners.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name ? `${m.display_name} (${m.email})` : m.email}</option>)}
            </select>
            <label htmlFor={`${id}-risk`}>Risk class</label>
            <select id={`${id}-risk`} value={form.risk_class ?? ""} onChange={(e) => set({ risk_class: (e.target.value || null) as RiskClass | null })}>
              <option value="">Not classified</option>
              {RISKS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <label htmlFor={`${id}-rationale`}>Why this risk class</label>
            <textarea id={`${id}-rationale`} value={form.risk_rationale} maxLength={2000} aria-invalid={needsReason || undefined}
                      aria-describedby={needsReason ? `${id}-rationale-help` : undefined}
                      onChange={(e) => set({ risk_rationale: e.target.value })} />
            {needsReason && <p id={`${id}-rationale-help`} className="error-text">Say why it has this risk class.</p>}
            <fieldset className="record-categories">
              <legend>Data it handles</legend>
              {CATEGORIES.map((c) => (
                <label key={c} className="row gap">
                  <input type="checkbox" checked={ticked(c)} onChange={(e) => tick(c, e.target.checked)} />{c}
                </label>
              ))}
              <label htmlFor={`${id}-other`}>Other (separate with commas)</label>
              <input id={`${id}-other`} value={other} onChange={(e) => { setOther(e.target.value); setStatus(null); }} />
            </fieldset>
            <label htmlFor={`${id}-audience`}>Its end users</label>
            <select id={`${id}-audience`} value={form.end_user_facing === null ? "" : String(form.end_user_facing)}
                    onChange={(e) => set({ end_user_facing: e.target.value === "" ? null : e.target.value === "true" })}>
              <option value="">Not stated</option>
              <option value="true">The public (customers, visitors)</option>
              <option value="false">Only our own staff</option>
            </select>
            <label htmlFor={`${id}-review`}>Next review</label>
            <input id={`${id}-review`} type="date" value={form.review_date ?? ""} onChange={(e) => set({ review_date: e.target.value || null })} />
          </fieldset>
          <div className="row gap wrap">
            {editable && <button type="button" className="small" disabled={save.isPending || needsReason} onClick={submit}>Save record</button>}
            <button type="button" className="small" disabled={download.isPending} onClick={() => download.mutate("markdown")}>Download (Markdown)</button>
            <button type="button" className="small" disabled={download.isPending} onClick={() => download.mutate("html")}>Download (HTML, prints as PDF)</button>
          </div>
          {status && <p className="muted" role="status">{status}</p>}
        </>
      )}
    </details>
  );
}
