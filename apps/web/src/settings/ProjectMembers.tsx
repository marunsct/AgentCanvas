/** Settings › Project › Members (E-10): the people and SCIM groups of this project, with their role here. Its admins
 * (and the organisation's) add, change and remove them; with the organisation's projects private, nobody else but
 * organisation admins sees the project. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { maturaMode } from "../api/auth";
import { apiFetch } from "../api/client";
import type { OrgSettingsOut, ProjectMemberOut } from "../api/generated";
import { PROJECT_ID, PROJECT_NAME } from "../api/queries";
import { useCan, type Role } from "../api/session";
import { confirmDialog, toast } from "../ui/dialog";
import { QueryStatus } from "../ui/QueryStatus";

const ROLES: Role[] = ["viewer", "reviewer", "builder", "admin"];

/** What the form names: an email is a person, anything else a SCIM group's name. */
export function principal(text: string): { email: string } | { group: string } {
  const t = text.trim();
  return /^[^@\s]+@[^@\s]+$/.test(t) ? { email: t } : { group: t };
}

export function ProjectMembers() {
  const qc = useQueryClient();
  const admin = useCan("admin") && !maturaMode();
  const key = ["project-members", PROJECT_ID];
  const q = useQuery({ queryKey: key, queryFn: () => apiFetch<ProjectMemberOut[]>(`/projects/${PROJECT_ID}/members`) });
  const security = useQuery({
    queryKey: ["org-settings"], queryFn: () => apiFetch<OrgSettingsOut>("/org/settings"),
  });
  const isPrivate = security.data?.security.private_projects === true;
  const [who, setWho] = useState("");
  const [role, setRole] = useState<Role>("builder");
  const set = useMutation({
    mutationFn: (body: Record<string, string>) =>
      apiFetch<ProjectMemberOut>(`/projects/${PROJECT_ID}/members`, { method: "PUT", json: body }),
    onSuccess: async (m) => {
      setWho("");
      await qc.invalidateQueries({ queryKey: key });
      toast(`${m.email ?? m.group} is ${m.role} in ${PROJECT_NAME}`);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => apiFetch(`/projects/${PROJECT_ID}/members/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
  const name = (m: ProjectMemberOut) => (m.kind === "group" ? `group ${m.group}` : m.display_name ?? m.email ?? m.user_id ?? "");
  const error = (set.error ?? remove.error) as Error | null;
  return (
    <section className="settings-card" aria-label="Project members">
      <h2>Members</h2>
      <p className="muted small">
        {isPrivate
          ? "This organisation's projects are private: only its admins and the people and groups below see this project, each with the role given here."
          : "Everyone in the organisation sees this project with their organisation role. A role given here raises it, in this project only."}
        {" "}A role here may be above someone's organisation role. Groups are the ones your identity provider pushes over SCIM.
      </p>
      {maturaMode() && <p className="muted" data-testid="managed-in-matura">In Matura mode, who sees what comes from Matura.</p>}
      {admin && (
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); set.mutate({ ...principal(who), role }); }}>
          <input required placeholder="name@example.com or a group's name" value={who} onChange={(e) => setWho(e.target.value)}
            aria-label="email or group" data-testid="project-member-who" />
          <select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="role">
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button type="submit" className="primary" disabled={set.isPending}>Add</button>
        </form>
      )}
      {error && <p className="error-text" role="alert">{error.message}</p>}
      <QueryStatus q={q} what="the project's members" />
      {q.data && q.data.length === 0 && (
        <p className="muted small" data-testid="no-project-members">
          No members of its own{isPrivate ? ": only organisation admins see this project" : ""}.
        </p>
      )}
      {q.data && q.data.length > 0 && (
        <table className="table" data-testid="project-members">
          <thead><tr><th>Member</th><th>Role here</th><th aria-label="actions" /></tr></thead>
          <tbody>
            {q.data.map((m) => (
              <tr key={m.id}>
                <td>{name(m)}{m.kind === "user" && m.display_name && <span className="muted small"> {m.email}</span>}</td>
                <td>
                  {admin
                    ? <select value={m.role} aria-label={`role of ${name(m)}`} disabled={set.isPending}
                        onChange={(e) => set.mutate({ ...(m.kind === "group" ? { group: m.group ?? "" } : { email: m.email ?? "" }), role: e.target.value })}>
                        {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    : m.role}
                </td>
                <td>{admin && (
                  <button type="button" className="link small" disabled={remove.isPending} onClick={async () => {
                    const body = isPrivate ? "They no longer see this project, unless a group or being an organisation admin still lets them." : undefined;
                    if (await confirmDialog({ title: `Remove ${name(m)} from ${PROJECT_NAME}?`, body, confirm: "Remove", danger: true })) remove.mutate(m.id);
                  }}>Remove</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!admin && !maturaMode() && <p className="muted small">Only this project's admins and the organisation's admins change its members.</p>}
    </section>
  );
}
