/** Export (S16): pick a version and where it will run; see what that needs before downloading the deploy kit.
 * S22: or pick another framework (preview, through Agent Spec) and see what does not carry over. */
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiDownload, apiFetch } from "../api/client";
import { useAgentFrameworks, useFrameworks } from "../api/queries";
import { useEditor } from "../editor/store";
import { portability } from "../ir/portability";
import type { BridgesOut, ExportPlanOut, GitHubPushOut, VersionOut } from "../api/generated";
import { QueryStatus } from "../ui/QueryStatus";
import { GitSync } from "./GitSync";

type Item = { name?: string; host?: string; port?: number; why?: string; where?: string; what?: string; install?: string; optional?: boolean };
type Prerequisites = {
  label: string; summary: string; tools: Item[]; keys: Item[]; deploy_settings: Item[]; hosts: Item[]; sizing: string;
  database: { needed: boolean; why: string; where: string; shared: boolean };
  knowledge?: { name: string; found: boolean }[]; // the knowledge bases the workflow searches
};

export function ExportSection({ wfId, versions }: { wfId: string; versions: VersionOut[] }) {
  const [open, setOpen] = useState(false);
  const [version, setVersion] = useState("");
  const [target, setTarget] = useState("local");
  // "" = the workflow's own framework; "fw:<id>" another installed one; "<bridge id>" through Agent Spec (S22)
  const [framework, setFramework] = useState("");
  const [error, setError] = useState<string | null>(null);
  const wf = useEditor((s) => s.wf);
  const { data: frameworks = [] } = useFrameworks();
  const { data: agents = [] } = useAgentFrameworks();
  const own = wf?.framework ?? "langchain";
  const fits = wf ? portability(wf, frameworks, new Set(agents.filter((a) => !a.hosted).map((a) => a.id))) : [];
  const as = framework.startsWith("fw:") ? framework.slice(3) : framework ? null : own; // a framework project
  const bridgeId = as === null ? framework : "";
  const service = as === "langchain"; // a LangGraph service: the deploy kit and "where it will run"
  const label = (id: string) => frameworks.find((f) => f.id === id)?.label ?? id;
  const query = new URLSearchParams({
    target, ...(version ? { version } : {}), ...(as && as !== own ? { framework: as } : {}),
  }).toString();
  const only = version ? `?version=${encodeURIComponent(version)}` : "";
  const plan = useQuery({
    queryKey: ["export-plan", wfId, version, target, as],
    enabled: open && service,
    queryFn: () => apiFetch<ExportPlanOut>(`/workflows/${wfId}/export/plan?${query}`),
  });
  const bridges = useQuery({
    queryKey: ["bridges", wfId, version],
    enabled: open,
    queryFn: () => apiFetch<BridgesOut>(`/workflows/${wfId}/bridges${only}`),
  });
  const bridge = bridges.data?.targets.find((t) => t.id === bridgeId);
  const p = plan.data?.prerequisites as Prerequisites | undefined;
  const download = () => {
    setError(null);
    const done = bridgeId
      ? apiDownload(`/workflows/${wfId}/bridges/${bridgeId}/export.zip${only}`, `export-${version || "draft"}-${bridgeId}.zip`)
      : apiDownload(`/workflows/${wfId}/export.zip?${query}`, `export-${version || "draft"}-${service ? target : as}.zip`);
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
        A project to run in your own environment: the workflow in the framework you pick, with its tests. A LangGraph
        service also gets the same API as a deployment, deploy files for the place you choose, and step-by-step guides
        with this workflow's keys and settings filled in.
      </p>
      <label htmlFor="export-version">Version</label>
      <select id="export-version" value={version} onChange={(e) => setVersion(e.target.value)}>
        <option value="">Current draft</option>
        {versions.map((v) => <option key={v.id} value={v.semver}>v{v.semver}{v.message ? `: ${v.message}` : ""}</option>)}
      </select>
      <label htmlFor="export-framework">Framework</label>
      <select id="export-framework" value={framework} onChange={(e) => setFramework(e.target.value)}>
        <option value="">{label(own)}{own === "langchain" ? " service, with the deploy kit" : " project"} (this workflow's framework)</option>
        {fits.some((f) => f.id !== own) && (
          <optgroup label="Other workflow frameworks (the same workflow, written for it)">
            {fits.filter((f) => f.id !== own).map((f) => (
              <option key={f.id} value={`fw:${f.id}`} disabled={!f.ok}>
                {f.label}{f.ok ? "" : ` (cannot run: ${f.blockers.map((b) => b.name).join(", ")})`}
              </option>
            ))}
          </optgroup>
        )}
        {(bridges.data?.targets ?? []).length > 0 && (
          <optgroup label="Through Agent Spec (preview)">
            {(bridges.data?.targets ?? []).map((t) => (
              <option key={t.id} value={t.id}>{t.label} (preview{t.runs ? "" : ", needs work"})</option>
            ))}
          </optgroup>
        )}
      </select>
      <QueryStatus q={bridges} what="the other frameworks" />
      {bridge && <BridgeGaps bridge={bridge} notOffered={bridges.data?.not_offered ?? []} />}
      {as && !service && (
        <p className="small muted" data-testid="export-framework-note">
          A {label(as)} project: the workflow&apos;s code, a runner, pinned packages, a README and tests that need no
          API key. Where it runs is up to you: the deploy kit&apos;s targets are for LangGraph services.
          {as !== own && " The workflow itself stays as it is; this only writes it for another framework."}
        </p>
      )}
      {service && (
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
      {service && p && (
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
          {(p.knowledge ?? []).length > 0 && (
            <>
              <h4>Knowledge bases it searches</h4>
              <ul data-testid="export-knowledge">
                {(p.knowledge ?? []).map((k) => (
                  <li key={k.name}>
                    <code>{k.name}</code>:{" "}
                    {k.found
                      ? "included in the export"
                      : <b className="error-text">not in this project, so it is left out and the exported search will fail until you add knowledge/{k.name}.jsonl</b>}
                  </li>
                ))}
              </ul>
            </>
          )}
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
      {as === own && <GitHubPush wfId={wfId} version={version} target={target} />}
      {as === own && <GitSync wfId={wfId} version={version} target={target} />}
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

/** S31: push the same export to a GitHub repository as one commit (a new branch is ready for a pull request). */
function GitHubPush({ wfId, version, target }: { wfId: string; version: string; target: string }) {
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("agentcanvas/export");
  const [folder, setFolder] = useState("");
  const push = useMutation({
    mutationFn: () => apiFetch<GitHubPushOut>(`/workflows/${wfId}/github`, {
      method: "POST", json: { repo: repo.trim(), branch: branch.trim(), folder: folder.trim(), target, version: version || null },
    }),
  });
  return (
    <details className="github-push small" data-testid="github-push">
      <summary>Push to GitHub…</summary>
      <p className="muted">
        Commits this export to a repository. It needs a project secret <code>GITHUB_TOKEN</code>: a GitHub token with
        Contents read and write on that repository. A new branch starts from the default branch, ready for a pull request.
      </p>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); push.mutate(); }}>
        <label htmlFor="gh-repo">Repository</label>
        <input id="gh-repo" placeholder="owner/name" value={repo} onChange={(e) => setRepo(e.target.value)} required />
        <label htmlFor="gh-branch">Branch</label>
        <input id="gh-branch" value={branch} onChange={(e) => setBranch(e.target.value)} required />
        <label htmlFor="gh-folder">Folder (empty: the repository root)</label>
        <input id="gh-folder" placeholder="agents/support" value={folder} onChange={(e) => setFolder(e.target.value)} />
        <button type="submit" className="primary" data-testid="github-push-button" disabled={push.isPending || !repo.trim()}>
          {push.isPending ? "Pushing…" : "Push"}</button>
      </form>
      {push.error && <p className="error-text" role="alert">{(push.error as Error).message}</p>}
      {push.data && (
        <p data-testid="github-pushed">
          Pushed {push.data.files} files to <a href={push.data.url} target="_blank" rel="noreferrer">{push.data.branch}</a>
          {push.data.created_branch ? " (a new branch: open a pull request from it on GitHub)" : ""}.
        </p>
      )}
    </details>
  );
}
