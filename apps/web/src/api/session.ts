/** Who is signed in and their role in the current organisation (M7-T3), loaded once at startup. */
import { apiFetch } from "./client";

export type Role = "viewer" | "reviewer" | "builder" | "admin";
const RANK: Record<Role, number> = { viewer: 0, reviewer: 1, builder: 2, admin: 3 };

export interface Me {
  id: string;
  email: string;
  org_id: string;
  role: Role;
  memberships: Record<string, Role>;
}

// dev-mode default until `loadMe` answers (and in unit tests)
export let ME: Me = { id: "usr_dev", email: "", org_id: "org_default", role: "admin", memberships: {} };

export async function loadMe(): Promise<Me> {
  ME = await apiFetch<Me>("/me");
  return ME;
}

/** Whether the signed-in user's role is at least `role` (the API enforces the same rule). */
export function can(role: Role): boolean {
  return RANK[ME.role] >= RANK[role];
}
