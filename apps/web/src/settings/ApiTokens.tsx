/** Settings › You › API tokens (G-13): a person's own tokens for scripts and CI. A token acts as them in this
 * organisation with at most the role picked; it is shown once, when made. Admins also see and revoke everyone's. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut, TokenCreated, TokenOut } from "../api/generated";
import { useCan, useMe, type Role } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";
import { ago, exact } from "../ui/time";

const ROLES: Role[] = ["viewer", "reviewer", "builder", "admin"];
const EXPIRY: [string, number | null][] = [["30 days", 30], ["90 days", 90], ["1 year", 365], ["Never", null]];

/** The lifetimes offered when the organisation allows at most `cap` days (0: no limit, and "Never" too). */
export function expiryChoices(cap: number): [string, number | null][] {
  if (!cap) return EXPIRY;
  const within = EXPIRY.filter(([, n]) => n !== null && n <= cap);
  return within.some(([, n]) => n === cap) ? within : [...within, [`${cap} days`, cap]];
}

/** The roles a token may have: up to the person's own. */
export function tokenRoles(mine: Role): Role[] {
  return ROLES.slice(0, ROLES.indexOf(mine) + 1);
}

/** "active", "revoked" or "expired" at `now`. */
export function tokenState(t: Pick<TokenOut, "revoked_at" | "expires_at">, now = Date.now()): "active" | "revoked" | "expired" {
  if (t.revoked_at) return "revoked";
  if (t.expires_at && new Date(t.expires_at).getTime() <= now) return "expired";
  return "active";
}

export function ApiTokens() {
  const qc = useQueryClient();
  const mine = useMe((s) => s.me.role);
  const admin = useCan("admin");
  const list = useQuery({ queryKey: ["tokens", "me"], queryFn: () => apiFetch<TokenOut[]>("/me/tokens") });
  const everyone = useQuery({
    queryKey: ["tokens", "org"], queryFn: () => apiFetch<TokenOut[]>("/org/tokens"), enabled: admin,
  });
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>(mine);
  const settings = useQuery({ queryKey: ["org-settings"], queryFn: () => apiFetch<OrgSettingsOut>("/org/settings") });
  const cap = Number((settings.data?.limits.effective as Record<string, unknown> | undefined)?.max_token_days ?? 0);
  const choices = expiryChoices(cap);
  const [picked, setDays] = useState<number | null>(90);
  const days = choices.some(([, n]) => n === picked) ? picked : choices[choices.length - 1][1]; // the longest allowed
  const [made, setMade] = useState<TokenCreated | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["tokens"] });
  const create = useMutation({
    mutationFn: () => apiFetch<TokenCreated>("/me/tokens", { method: "POST", json: { name: name.trim(), role, expires_in_days: days } }),
    onSuccess: (t) => { setMade(t); setName(""); refresh(); },
  });
  const revoke = useMutation({
    mutationFn: ({ id, path }: { id: string; path: "me" | "org" }) => apiFetch(`/${path}/tokens/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => { toast("Revoked the token"); refresh(); },
  });
  const ask = async (t: TokenOut, path: "me" | "org") => {
    if (await confirmDialog({ title: `Revoke "${t.name}"?`, body: "Scripts that use it stop working at once.", confirm: "Revoke", danger: true })) {
      revoke.mutate({ id: t.id, path });
    }
  };
  const table = (rows: TokenOut[], path: "me" | "org") => (
    <table className="table" aria-label={path === "me" ? "your tokens" : "everyone's tokens"}>
      <thead><tr><th>Name</th>{path === "org" && <th>Person</th>}<th>Token</th><th>Role</th><th>Expires</th><th>Last used</th><th aria-label="actions" /></tr></thead>
      <tbody>
        {rows.map((t) => {
          const state = tokenState(t);
          return (
            <tr key={t.id} className={state === "active" ? "" : "muted"}>
              <td>{t.name}</td>
              {path === "org" && <td>{t.email}</td>}
              <td><code>{t.prefix}…</code></td>
              <td>{t.role}</td>
              <td title={t.expires_at ? exact(t.expires_at) : undefined}>
                {state === "revoked" ? "revoked" : state === "expired" ? "expired" : t.expires_at ? new Date(t.expires_at).toLocaleDateString() : "never"}
              </td>
              <td>{t.last_used_at ? ago(t.last_used_at) : "never"}</td>
              <td className="actions">{state === "active" && (
                <button type="button" className="link small" aria-label={`revoke ${t.name}`} disabled={revoke.isPending}
                  onClick={() => void ask(t, path)}>Revoke</button>
              )}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
  return (
    <section className="settings-card" aria-label="API tokens">
      <h2>API tokens</h2>
      <p className="muted small">
        For scripts and CI: send <code>Authorization: Bearer act_…</code> to the API. A token acts as you in this
        organisation, with at most the role you pick (and never more than your role now). Removing you from the
        organisation stops your tokens.
      </p>
      {made && (
        <div className="callout" role="status" aria-label="new token">
          <p><strong>Copy your new token now.</strong> It is not shown again.</p>
          <div className="row gap">
            <code className="mono" data-testid="new-token">{made.token}</code>
            <button type="button" onClick={() => void navigator.clipboard?.writeText(made.token).then(() => toast("Copied"))}>Copy</button>
            <button type="button" className="link small" onClick={() => setMade(null)}>Done</button>
          </div>
        </div>
      )}
      <form className="row gap wrap" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <input placeholder="What it is for, e.g. CI pipeline" value={name} maxLength={100} aria-label="token name"
          onChange={(e) => setName(e.target.value)} />
        <label className="row">Role
          <select aria-label="token role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {tokenRoles(mine).map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="row">Expires
          <select aria-label="token expiry" value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
            {choices.map(([label, n]) => <option key={label} value={n ?? ""}>{label}</option>)}
          </select>
        </label>
        <button type="submit" className="primary" disabled={!name.trim() || create.isPending}>Create token</button>
      </form>
      {create.error && <p className="error-text small" role="alert">Could not create the token: {(create.error as Error).message}</p>}
      <QueryStatus q={list} what="your tokens" />
      {list.data && list.data.length === 0 && <p className="empty-state">No tokens yet.</p>}
      {list.data && list.data.length > 0 && table(list.data, "me")}
      {admin && (
        <>
          <h3>Everyone's tokens</h3>
          <p className="muted small">Admins can revoke any member's token, for example one that leaked.</p>
          <QueryStatus q={everyone} what="the organisation's tokens" />
          {everyone.data && everyone.data.length === 0 && <p className="empty-state">Nobody has a token.</p>}
          {everyone.data && everyone.data.length > 0 && table(everyone.data, "org")}
        </>
      )}
      {revoke.error && <p className="error-text small" role="alert">Could not revoke the token: {(revoke.error as Error).message}</p>}
    </section>
  );
}
