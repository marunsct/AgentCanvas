/** Members (M7-T3): who belongs to the organisation and with which role. Admins invite, change roles and remove.
 * With Matura (TS9a) the list is read-only here: members and roles are managed in Matura's portal. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { maturaMode } from "../api/auth";
import { apiFetch } from "../api/client";
import { orgPortalUrl, useCan, useMe, type Role } from "../api/session";
import { QueryStatus } from "../ui/QueryStatus";
import type { MemberOut as Member } from "../api/generated";
import { confirmDialog } from "../ui/dialog";


const ROLES: { role: Role; what: string }[] = [
  { role: "viewer", what: "sees workflows and runs" },
  { role: "reviewer", what: "also answers approvals in the Inbox" },
  { role: "builder", what: "also builds, runs and publishes" },
  { role: "admin", what: "also manages members" },
];

export function MembersPage() {
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ["members"], queryFn: () => apiFetch<Member[]>("/org/members") });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("builder");
  const done = () => void qc.invalidateQueries({ queryKey: ["members"] });
  const set = useMutation({
    mutationFn: (body: { email: string; role: Role }) => apiFetch<Member>("/org/members", { method: "PUT", json: body }),
    onSuccess: () => { setEmail(""); done(); },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/org/members/${id}`, { method: "DELETE" }),
    onSuccess: done,
  });
  const me = useMe((s) => s.me);
  const admin = useCan("admin") && !maturaMode();
  const portal = orgPortalUrl("members");
  const error = (set.error ?? remove.error) as Error | null;
  return (
    <section className="settings-card members">
      <h2>Members</h2>
      <p className="muted small">Who belongs to this organisation, and what each role may do.</p>
      <ul className="small muted">{ROLES.map((r) => <li key={r.role}><b>{r.role}</b> {r.what}</li>)}</ul>
      {maturaMode() && (
        <p className="muted" data-testid="managed-in-matura">
          Members and roles are managed in Matura{portal ? <>: <a href={portal} target="_blank" rel="noreferrer" data-testid="portal-link">open the portal</a></> : "."}
        </p>
      )}
      {admin && (
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); set.mutate({ email: email.trim(), role }); }}>
          <input type="email" required placeholder="name@example.com" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="email" />
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="role">
            {ROLES.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
          </select>
          <button type="submit" className="primary" disabled={set.isPending}>Invite</button>
        </form>
      )}
      {error && <p className="error-text" role="alert">{error.message}</p>}
      <QueryStatus q={members} what="members" />
      <table className="members" data-testid="members">
        <tbody>
          {(members.data ?? []).map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name ?? m.email}{m.display_name && <span className="muted small"> {m.email}</span>}</td>
              <td>{m.invited || !m.accepted ? <span className="muted small">{m.invited ? "invited" : "awaiting acceptance"}</span> : null}</td>
              <td>
                {admin && m.user_id !== me.id
                  ? <select value={m.role} aria-label={`role of ${m.email}`} onChange={(e) => set.mutate({ email: m.email, role: e.target.value as Role })}>
                      {ROLES.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
                    </select>
                  : m.role}
              </td>
              <td>{admin && m.user_id !== me.id && (
                <button type="button" className="link small" onClick={async () => { if (await confirmDialog({ title: `Remove ${m.email}?`, body: "They lose access to this organisation at once.", confirm: "Remove", danger: true })) remove.mutate(m.user_id); }}>Remove</button>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
