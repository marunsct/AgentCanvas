/** Export (S16): pick a version and where it will run; see what that needs before downloading the deploy kit. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import type { ExportPlanOut, VersionOut } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";

type Item = { name?: string; host?: string; port?: number; why?: string; where?: string; what?: string; install?: string; optional?: boolean };
type Prerequisites = {
  label: string; summary: string; tools: Item[]; keys: Item[]; deploy_settings: Item[]; hosts: Item[]; sizing: string;
  database: { needed: boolean; why: string; where: string; shared: boolean };
};

export function ExportSection({ wfId, versions }: { wfId: string; versions: VersionOut[] }) {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState("");
  const [target, setTarget] = useState("local");
  const [error, setError] = useState<string | null>(null);
  const query = new URLSearchParams({ target, ...(version ? { version } : {}) }).toString();
  const plan = useQuery({
    queryKey: ["export-plan", wfId, version, target],
    enabled: open,
    queryFn: () => apiFetch<ExportPlanOut>(`/workflows/${wfId}/export/plan?${query}`),
  });
  const p = plan.data?.prerequisites as Prerequisites | undefined;
  const download = () => {
    setError(null);
    apiDownload(`/workflows/${wfId}/export.zip?${query}`, `export-${version || "draft"}-${target}.zip`)
      .catch((e: Error) => setError(e.message));
  };

  if (!open) {
    return (
      <p className="small">
        <button type="button" className="link" data-testid="export-open" onClick={() => setOpen(true)}>
          ⬇ Export to run in your own environment…</button>
      </p>
    );
  }
  return (
    <section className="export-box" aria-label="Export" data-testid="export-box">
      <div className="row gap">
        <b>Export</b><span className="spacer" />
        <button type="button" className="link small" onClick={() => setOpen(false)}>close</button>
      </div>
      <p className="small muted">
        A ready-to-deploy project: the workflow as a service with the same API as a deployment, deploy files for the
        place you choose, and step-by-step guides with this workflow's keys and settings filled in.
      </p>
      <label htmlFor="export-version">Version</label>
      <select id="export-version" value={version} onChange={(e) => setVersion(e.target.value)}>
        <option value="">Current draft</option>
        {versions.map((v) => <option key={v.id} value={v.semver}>v{v.semver}{v.message ? `: ${v.message}` : ""}</option>)}
      </select>
      <label htmlFor="export-target">Where it will run</label>
      <select id="export-target" value={target} onChange={(e) => setTarget(e.target.value)}>
        {(plan.data?.targets ?? [{ id: "local", label: "Your computer (Docker)" }]).map((t) => (
          <option key={t.id} value={t.id}>{t.label}</option>
        ))}
      </select>
      <QueryStatus q={plan} what="what the export needs" />
      {p && (
        <div className="export-needs small" data-testid="export-needs">
          <p>{p.summary}</p>
          <h4>You will need</h4>
          <ul>
            {p.tools.map((t) => <li key={t.name}><b>{t.name}</b>{t.optional ? " (optional)" : ""}: {t.why}</li>)}
            {p.keys.map((k) => <li key={k.name}><code>{k.name}</code>: {k.where}</li>)}
            {p.deploy_settings.map((d) => <li key={d.name}><code>{d.name}</code>: {d.what}</li>)}
            <li>
              Database: {p.database.needed ? <b>needed</b> : "optional"} ({p.database.why}). {p.database.where}.
            </li>
          </ul>
          {p.hosts.length > 0 && (
            <>
              <h4>It connects to</h4>
              <ul>{p.hosts.map((h) => <li key={h.host}><code>{h.host}</code>: {h.why}</li>)}</ul>
            </>
          )}
          <p className="muted">{p.sizing} After unzipping, <code>make doctor</code> checks all of this.</p>
        </div>
      )}
      <div className="row gap">
        <button type="button" className="primary" data-testid="export-download" onClick={download}>⬇ Download</button>
      </div>
      {error && <p className="error-text small" role="alert">{error}</p>}
    </section>
  );
}
