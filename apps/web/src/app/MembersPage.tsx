/** Members (M7-T3): who belongs to the organisation and with which role. Admins invite, change roles and remove. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../api/client";
import { can, ME, type Role } from "../api/session";
import { QueryStatus } from "../ui/QueryStatus";

interface Member { user_id: string; email: string; display_name: string | null; role: Role; invited: boolean }

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
  const admin = can("admin");
  const error = (set.error ?? remove.error) as Error | null;
  return (
    <div className="page">
      <header className="topbar"><Link to="/" className="brand">AgentCanvas</Link><span className="crumb">/ Members</span></header>
      <ul className="small muted">{ROLES.map((r) => <li key={r.role}><b>{r.role}</b> {r.what}</li>)}</ul>
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
              <td>{m.invited ? <span className="muted small">invited</span> : null}</td>
              <td>
                {admin && m.user_id !== ME.id
                  ? <select value={m.role} aria-label={`role of ${m.email}`} onChange={(e) => set.mutate({ email: m.email, role: e.target.value as Role })}>
                      {ROLES.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
                    </select>
                  : m.role}
              </td>
              <td>{admin && m.user_id !== ME.id && (
                <button type="button" className="link small" onClick={() => { if (window.confirm(`Remove ${m.email}?`)) remove.mutate(m.user_id); }}>Remove</button>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
