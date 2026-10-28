/** Policies (FR-X-19): organisation rules over every workflow, written as CEL. The editor flags breaches and
 * publishing refuses them. Everyone can read them; admins add, switch off and delete. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { maturaMode } from "../api/auth";
import { can, orgPortalUrl } from "../api/session";
import { QueryStatus } from "../ui/QueryStatus";
import { ThemeSwitcher } from "../theme/ThemeSwitcher";
import { confirmDialog } from "../ui/dialog";

type Scope = "node" | "resource" | "workflow";
interface PolicyIn {
  name: string; description: string; scope: Scope; match: string; require: string;
  severity: "error" | "warning"; environments: string[]; enabled: boolean;
}
interface Policy extends PolicyIn { id: string; created_at: string }

const EMPTY: PolicyIn = {
  name: "", description: "", scope: "node", match: "true", require: "", severity: "error", environments: [], enabled: true,
};

// starting points for the two rules most organisations want first
const EXAMPLES: PolicyIn[] = [
  {
    ...EMPTY, name: "Writes need approval", description: "a person approves every external write",
    require: 'node.effects.all(e, e.effect != "external_write" || e.gated)',
  },
  {
    ...EMPTY, name: "Approved models in prod", scope: "resource", match: 'resource.type == "model.chat"',
    require: 'resource.config.model in ["anthropic:claude-sonnet-5-5"]', environments: ["prod"],
  },
];

export function PoliciesPage() {
  const qc = useQueryClient();
  const policies = useQuery({ queryKey: ["policies"], queryFn: () => apiFetch<Policy[]>("/org/policies") });
  const [draft, setDraft] = useState<PolicyIn>(EMPTY);
  const done = () => void qc.invalidateQueries({ queryKey: ["policies"] });
  const create = useMutation({
    mutationFn: (body: PolicyIn) => apiFetch<Policy>("/org/policies", { method: "POST", json: body }),
    onSuccess: () => { setDraft(EMPTY); done(); },
  });
  const update = useMutation({
    mutationFn: ({ id, ...body }: Policy) => apiFetch<Policy>(`/org/policies/${id}`, { method: "PUT", json: body }),
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/org/policies/${id}`, { method: "DELETE" }),
    onSuccess: done,
  });
  const admin = can("admin");
  const error = (create.error ?? update.error ?? remove.error) as Error | null;
  const edit = (patch: Partial<PolicyIn>) => setDraft((d) => ({ ...d, ...patch }));
  const portal = orgPortalUrl();
  return (
    <div className="page">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Policies</span><span className="spacer" /><ThemeSwitcher /></header>
      {maturaMode() && (
        <p className="muted" data-testid="org-settings-in-matura">
          Sign-in, single sign-on, security and member settings of this organisation are in Matura
          {portal ? <>: <a href={portal} target="_blank" rel="noreferrer" data-testid="portal-link">open its settings</a></> : "."}
        </p>
      )}
      <p className="small muted">
        Each policy checks every step, resource or workflow its <b>match</b> picks and requires <b>require</b> to hold.
        Policies for named environments block publishing there and show as warnings in the editor.
        Names: <code>node</code> (<code>type</code>, <code>config</code>, <code>effects</code>, <code>ungated</code>,{" "}
        <code>models</code>, <code>tools</code>), <code>resource</code>, <code>workflow</code>, <code>env</code>.
      </p>
      {admin && (
        <form className="policy-form" onSubmit={(e) => { e.preventDefault(); create.mutate(draft); }}>
          <div className="row gap small">
            Start from:
            {EXAMPLES.map((x) => <button key={x.name} type="button" className="link small" onClick={() => setDraft(x)}>{x.name}</button>)}
          </div>
          <div className="row gap">
            <input required placeholder="Name" aria-label="policy name" value={draft.name} onChange={(e) => edit({ name: e.target.value })} />
            <select aria-label="scope" value={draft.scope} onChange={(e) => edit({ scope: e.target.value as Scope })}>
              <option value="node">each step</option><option value="resource">each resource</option><option value="workflow">the workflow</option>
            </select>
            <select aria-label="severity" value={draft.severity} onChange={(e) => edit({ severity: e.target.value as PolicyIn["severity"] })}>
              <option value="error">error</option><option value="warning">warning</option>
            </select>
            <input placeholder="environments (empty: always)" aria-label="environments" value={draft.environments.join(", ")}
              onChange={(e) => edit({ environments: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <input placeholder="match (CEL)" aria-label="match" value={draft.match} onChange={(e) => edit({ match: e.target.value })} />
          <input required placeholder="require (CEL)" aria-label="require" value={draft.require} onChange={(e) => edit({ require: e.target.value })} />
          <input placeholder="What it means (shown with every breach)" aria-label="description" value={draft.description}
            onChange={(e) => edit({ description: e.target.value })} />
          <div><button type="submit" className="primary" disabled={create.isPending}>Add policy</button></div>
        </form>
      )}
      {error && <p className="error-text" role="alert">{error.message}</p>}
      <QueryStatus q={policies} what="policies" />
      {policies.data?.length === 0 && <p className="muted">No policies yet.</p>}
      <table className="members" data-testid="policies">
        <tbody>
          {(policies.data ?? []).map((p) => (
            <tr key={p.id}>
              <td>
                <b>{p.name}</b> <span className="muted small">{p.enabled ? "" : "off · "}{p.severity}{p.environments.length ? ` · ${p.environments.join(", ")}` : ""}</span>
                {p.description && <div className="small">{p.description}</div>}
                <div className="small muted"><code>{p.scope}</code>{p.match !== "true" && <> where <code>{p.match}</code></>}: <code>{p.require}</code></div>
              </td>
              <td>
                {admin
                  ? <button type="button" className="link small" disabled={update.isPending}
                      onClick={() => update.mutate({ ...p, enabled: !p.enabled })}>{p.enabled ? "Switch off" : "Switch on"}</button>
                  : !p.enabled && <span className="muted small">off</span>}
              </td>
              <td>{admin && (
                <button type="button" className="link small" onClick={async () => { if (await confirmDialog({ title: `Delete the policy ${p.name}?`, confirm: "Delete", danger: true })) remove.mutate(p.id); }}>Delete</button>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
