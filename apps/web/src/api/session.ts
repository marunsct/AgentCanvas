/** Who is signed in and their role in the current organisation (M7-T3), loaded once at startup, and their role in the
 * project the app works in (E-10), known once the project is chosen. */
import { create } from "zustand";
import { portalUrl } from "./auth";
import { apiFetch } from "./client";
import type { Preferences } from "./generated";
import { applyPreferences } from "./preferences";

export type Role = "viewer" | "reviewer" | "builder" | "admin";
const RANK: Record<Role, number> = { viewer: 0, reviewer: 1, builder: 2, admin: 3 };

export interface Me {
  id: string;
  email: string;
  org_id: string;
  role: Role;
  memberships: Record<string, Role>;
  org_names?: Record<string, string>;
  portal_url?: string | null; // Matura mode: this organisation's page in Matura's portal (TS26)
  preferences?: Preferences;
  /** the role in the current project (E-10): it can differ from `role`, the organisation's */
  project_role?: Role;
}

// dev-mode default until `loadMe` answers (and in unit tests)
const DEFAULT_ME: Me = { id: "usr_dev", email: "", org_id: "org_default", role: "admin", memberships: {} };

/** The session as state: components that must redraw when the user, organisation or role changes use `useMe`. */
export const useMe = create<{ me: Me; loaded: boolean }>(() => ({ me: DEFAULT_ME, loaded: false }));

/** The current session for code outside React (a live binding to the store's value; read-only). */
export let ME: Me = DEFAULT_ME;
useMe.subscribe((s) => { ME = s.me; });

export async function loadMe(): Promise<Me> {
  const me = await apiFetch<Me>("/me");
  useMe.setState({ me, loaded: true });
  void applyPreferences(me.preferences);
  return me;
}

/** The role that counts for what is in the current project: the project's, else the organisation's. */
export function projectRole(me: Me): Role {
  return me.project_role ?? me.role;
}

/** Remember the role in the project just chosen. */
export function setProjectRole(role: Role | null | undefined): void {
  useMe.setState((s) => ({ me: { ...s.me, project_role: role ?? undefined } }));
}

/** `can`, for components: redraws when the role changes. */
export function useCan(role: Role): boolean {
  return RANK[useMe((s) => projectRole(s.me))] >= RANK[role];
}

/** Whether the signed-in user's role in the current project is at least `role` (the API enforces the same rule). */
export function can(role: Role): boolean {
  return RANK[projectRole(ME)] >= RANK[role];
}

/** `canOrg`, for components: for the organisation's own pages (members, security, spending, audit log...). */
export function useCanOrg(role: Role): boolean {
  return RANK[useMe((s) => s.me.role)] >= RANK[role];
}

/** Whether the signed-in user's role in the organisation is at least `role`. */
export function canOrg(role: Role): boolean {
  return RANK[ME.role] >= RANK[role];
}

/** A page of the current organisation in Matura's customer portal (`members`, or "" for its overview with SSO and
 * security settings); null outside Matura mode or when the API knows no portal. */
export function orgPortalUrl(page = ""): string | null {
  const base = portalUrl();
  if (base === null) return null;
  return (ME.portal_url ?? `${base.replace(/\/?$/, "/")}t/${encodeURIComponent(ME.org_id)}/`) + page;
}
