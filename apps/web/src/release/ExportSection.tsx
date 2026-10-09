/** Export (S16): pick a version and where it will run; see what that needs before downloading the deploy kit.
 * S22: or pick another framework (preview, through Agent Spec) and see what does not carry over. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import type { BridgesOut, ExportPlanOut, VersionOut } from "../api/generated";
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
  const [framework, setFramework] = useState("");  // "" = the AgentCanvas service; else a bridge (S22)
  const [error, setError] = useState<string | null>(null);
  const query = new URLSearchParams({ target, ...(version ? { version } : {}) }).toString();
  const only = version ? `?version=${encodeURIComponent(version)}` : "";
  const plan = useQuery({
    queryKey: ["export-plan", wfId, version, target],
    enabled: open && !framework,
    queryFn: () => apiFetch<ExportPlanOut>(`/workflows/${wfId}/export/plan?${query}`),
  });
  const bridges = useQuery({
    queryKey: ["bridges", wfId, version],
    enabled: open,
    queryFn: () => apiFetch<BridgesOut>(`/workflows/${wfId}/bridges${only}`),
  });
  const bridge = bridges.data?.targets.find((t) => t.id === framework);
  const p = plan.data?.prerequisites as Prerequisites | undefined;
  const download = () => {
    setError(null);
    const done = framework
      ? apiDownload(`/workflows/${wfId}/bridges/${framework}/export.zip${only}`, `export-${version || "draft"}-${framework}.zip`)
      : apiDownload(`/workflows/${wfId}/export.zip?${query}`, `export-${version || "draft"}-${target}.zip`);
    done.catch((e: Error) => setError(e.message));
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
      <label htmlFor="export-framework">Framework</label>
      <select id="export-framework" value={framework} onChange={(e) => setFramework(e.target.value)}>
        <option value="">AgentCanvas service (LangGraph, with the deploy kit)</option>
        {(bridges.data?.targets ?? []).map((t) => (
          <option key={t.id} value={t.id}>{t.label} (preview{t.runs ? "" : ", needs work"})</option>
        ))}
      </select>
      <QueryStatus q={bridges} what="the other frameworks" />
      {bridge && <BridgeGaps bridge={bridge} notOffered={bridges.data?.not_offered ?? []} />}
      {!framework && (
        <>
          <label htmlFor="export-target">Where it will run</label>
          <select id="export-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            {(plan.data?.targets ?? [{ id: "local", label: "Your computer (Docker)" }]).map((t) => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
          <QueryStatus q={plan} what="what the export needs" />
        </>
      )}
      {!framework && p && (
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

/** S22: what does not carry over to another framework; "must fix" items stop the flow from running there. */
function BridgeGaps({ bridge, notOffered }: { bridge: BridgesOut["targets"][number]; notOffered: BridgesOut["not_offered"] }) {
  const must = bridge.gaps.filter((g) => g.blocks);
  const differs = bridge.gaps.filter((g) => !g.blocks);
  return (
    <div className="export-needs small" data-testid="bridge-gaps">
      <p>
        <b>Preview.</b> {bridge.summary} The project holds the workflow as an Agent Spec flow, a runner, pinned
        packages ({bridge.packages.join(", ")}), a README and tests that need no API key.
      </p>
      {must.length > 0 ? (
        <>
          <h4>Before it runs there</h4>
          <ul>{must.map((g) => <li key={g.text}>{g.text}</li>)}</ul>
        </>
      ) : <p>Nothing stops this workflow from running there.</p>}
      {differs.length > 0 && (
        <>
          <h4>What works differently</h4>
          <ul>{differs.map((g) => <li key={g.text}>{g.text}</li>)}</ul>
        </>
      )}
      <p className="muted">
        Not offered: {notOffered.map((n) => `${n.label} (${n.why})`).join("; ")}. A single AI agent exports to nine
        frameworks from its own settings.
      </p>
    </div>
  );
}
