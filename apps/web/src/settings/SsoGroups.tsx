/** Settings › Organisation › General › Single sign-on groups (G-13): which role each group of the identity provider
 * gives, applied at every sign-in. Shown only where people sign in with OpenID Connect. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { GroupMapping, OrgSettingsOut } from "../api/generated";
import { toast } from "../ui/dialog";

type Role = "viewer" | "reviewer" | "builder" | "admin";
const ROLES: Role[] = ["viewer", "reviewer", "builder", "admin"];
export type Row = { group: string; role: Role };

/** The table the API stores: blank group names dropped, a repeated group keeps its last role. */
export function rowsToRoles(rows: Row[]): Record<string, Role> {
  const out: Record<string, Role> = {};
  for (const r of rows) if (r.group.trim()) out[r.group.trim()] = r.role;
  return out;
}

export function SsoGroups({ data, admin }: { data: OrgSettingsOut; admin: boolean }) {
  const qc = useQueryClient();
  const [claim, setClaim] = useState("groups");
  const [unmatched, setUnmatched] = useState<"keep" | "remove">("keep");
  const [rows, setRows] = useState<Row[]>([]);
  useEffect(() => {
    const m = data.sso_groups;
    setClaim(m?.claim ?? "groups");
    setUnmatched(m?.unmatched ?? "keep");
    setRows(Object.entries(m?.roles ?? {}).map(([group, role]) => ({ group, role })));
  }, [data.sso_groups]);
  const save = useMutation({
    mutationFn: (sso_groups: GroupMapping | null) =>
      apiFetch<OrgSettingsOut>("/org/settings", { method: "PUT", json: { sso_groups } }),
    onSuccess: (out) => { qc.setQueryData(["org-settings"], out); toast("Saved the group mapping"); },
  });
  if (!data.sso_groups_editable) return null;
  const set = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <>
      <h3>Single sign-on groups</h3>
      <p className="muted small">
        Give roles by the groups your identity provider puts in the sign-in token. The role is set at every sign-in and
        replaces one set by hand; people in a mapped group join without an invitation. With no table, roles are set under Members.
      </p>
      <div className="row gap wrap">
        <label className="row">Claim
          <input aria-label="groups claim" value={claim} disabled={!admin} className="mono" style={{ width: 200 }}
            onChange={(e) => setClaim(e.target.value.trim())} />
        </label>
        <label className="row">Token without a mapped group
          <select aria-label="unmatched groups" value={unmatched} disabled={!admin} onChange={(e) => setUnmatched(e.target.value as "keep" | "remove")}>
            <option value="keep">keeps their role</option>
            <option value="remove">is removed from the organisation</option>
          </select>
        </label>
      </div>
      <table className="table" aria-label="group roles">
        <thead><tr><th>Group</th><th>Role</th><th aria-label="actions" /></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><input aria-label={`group ${i + 1}`} value={r.group} disabled={!admin} onChange={(e) => set(i, { group: e.target.value })} /></td>
              <td>
                <select aria-label={`role of group ${i + 1}`} value={r.role} disabled={!admin} onChange={(e) => set(i, { role: e.target.value as Role })}>
                  {ROLES.map((x) => <option key={x}>{x}</option>)}
                </select>
              </td>
              <td className="actions">{admin && <button type="button" className="link small" aria-label={`remove group ${i + 1}`}
                onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {admin && (
        <div className="row gap">
          <button type="button" onClick={() => setRows([...rows, { group: "", role: "viewer" }])}>Add group</button>
          <button type="button" className="primary" disabled={save.isPending || !claim}
            onClick={() => save.mutate(Object.keys(rowsToRoles(rows)).length ? { claim, unmatched, roles: rowsToRoles(rows) } : null)}>
            Save groups
          </button>
        </div>
      )}
      {save.error && <p className="error-text small" role="alert">Could not save: {(save.error as Error).message}</p>}
    </>
  );
}
