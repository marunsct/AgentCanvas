/** Members (M7-T3): who belongs to the organisation and with which role. Admins invite, change roles and remove.
 * With Matura (TS9a) the list is read-only here: members and roles are managed in Matura's portal. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { maturaMode } from "../api/auth";
import { apiDownload, apiFetch } from "../api/client";
import { orgPortalUrl, useCan, useMe, type Role } from "../api/session";
import { QueryStatus } from "../ui/QueryStatus";
import type { MemberOut as Member } from "../api/generated";
import { confirmDialog, promptDialog, toast } from "../ui/dialog";


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
  const erase = useMutation({
    mutationFn: ({ id, ...body }: { id: string; reason: string; conversations: boolean }) =>
      apiFetch<{ account_erased: boolean }>(`/org/members/${id}/erase`, { method: "POST", json: body }),
    onSuccess: (out) => { done(); toast(out.account_erased ? "Erased" : "Erased here; they keep their account in another organisation"); },
  });
  const exportData = useMutation({
    mutationFn: (id: string) => apiDownload(`/org/members/${id}/export`, `agentcanvas-${id}.json`),
  });
  const askErase = async (m: Member) => {
    const reason = await promptDialog({
      title: `Erase ${m.email}?`,
      body: "They are removed, their API tokens and sign-ins revoked, and their memories and Copilot chats deleted. Their name and email are removed unless they belong to another organisation. This cannot be undone.",
      confirm: "Next", danger: true,
      input: { label: "Reason (the request you are answering, kept in the audit log)", required: true },
    });
    if (!reason || !reason.trim()) return;
    const conversations = await confirmDialog({
      title: "Also delete the conversations they started?",
      body: "With the files they attached. Otherwise those stay with the project.",
      confirm: "Delete them too", cancel: "Keep them", danger: true,
    });
    erase.mutate({ id: m.user_id, reason: reason.trim(), conversations });
  };
  const me = useMe((s) => s.me);
  const admin = useCan("admin") && !maturaMode();
  const portal = orgPortalUrl("members");
  const error = (set.error ?? remove.error ?? erase.error ?? exportData.error) as Error | null;
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
      <table className="table members" data-testid="members">
        <thead><tr><th>Member</th><th>Status</th><th>Role</th><th aria-label="actions" /></tr></thead>
        <tbody>
          {(members.data ?? []).map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name ?? m.email}{m.display_name && <span className="muted small"> {m.email}</span>}</td>
              <td>{m.deactivated
                ? <span className="badge err" title="Suspended by your identity provider over SCIM; it can reactivate them">deactivated</span>
                : m.invited || !m.accepted
                ? <span className="badge warn" title="It takes effect when they next sign in">{m.invited ? "invited" : "awaiting acceptance"}</span>
                : <span className="badge ok">member</span>}</td>
              <td>
                {admin && m.user_id !== me.id
                  ? <select value={m.role} aria-label={`role of ${m.email}`} onChange={(e) => set.mutate({ email: m.email, role: e.target.value as Role })}>
                      {ROLES.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
                    </select>
                  : m.role}
              </td>
              <td>{admin && m.user_id !== me.id && (
                m.invited
                  ? <button type="button" className="link small" onClick={async () => { if (await confirmDialog({ title: `Cancel the invitation to ${m.email}?`, confirm: "Cancel invitation", cancel: "Keep it", danger: true })) remove.mutate(m.user_id); }}>Cancel invitation</button>
                  : <>
                      <button type="button" className="link small" onClick={async () => { if (await confirmDialog({ title: `Remove ${m.email}?`, body: "They lose access to this organisation at once.", confirm: "Remove", danger: true })) remove.mutate(m.user_id); }}>Remove</button>{" "}
                      <button type="button" className="link small" disabled={exportData.isPending} onClick={() => exportData.mutate(m.user_id)}>Export data</button>{" "}
                      <button type="button" className="link small" disabled={erase.isPending} onClick={() => void askErase(m)}>Erase…</button>
                    </>
              )}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
