// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const sent: { path: string; method?: string; json?: unknown }[] = [];
let members: unknown[] = [];
let isPrivate = true;
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string; json?: Record<string, string> }) => {
    if (path === "/org/settings") return { security: { private_projects: isPrivate } };
    if (init?.method) {
      sent.push({ path, method: init.method, json: init.json });
      return { id: "pmb_new", kind: init.json?.group ? "group" : "user", email: init.json?.email ?? null, group: init.json?.group ?? null, role: init.json?.role };
    }
    return members;
  }),
}));
vi.mock("../api/auth", () => ({ maturaMode: () => false }));
vi.mock("../api/queries", () => ({ PROJECT_ID: "prj_a", PROJECT_NAME: "Team A" }));
let admin = true;
vi.mock("../api/session", () => ({ useCan: () => admin }));
const dialog = vi.hoisted(() => ({ confirmDialog: vi.fn(async () => true), toast: vi.fn() }));
vi.mock("../ui/dialog", () => dialog);

const { ProjectMembers, principal } = await import("./ProjectMembers");

const ANA = { id: "pmb_1", kind: "user", user_id: "usr_ana", email: "ana@example.com", display_name: "Ana", group_id: null, group: null, role: "builder", created_at: "2026-11-10T00:00:00Z" };
const OPS = { id: "pmb_2", kind: "group", user_id: null, email: null, display_name: null, group_id: "sgr_1", group: "Ops", role: "viewer", created_at: "2026-11-10T00:00:00Z" };

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><ProjectMembers /></QueryClientProvider>);
}

afterEach(() => { cleanup(); sent.length = 0; admin = true; isPrivate = true; members = []; });

describe("ProjectMembers (E-10)", () => {
  it("tells an email from a group's name", () => {
    expect(principal(" ana@example.com ")).toEqual({ email: "ana@example.com" });
    expect(principal("Support team")).toEqual({ group: "Support team" });
  });

  it("lists people and groups with their role here, adds, changes and removes them", async () => {
    members = [ANA, OPS];
    show();
    expect(await screen.findByText("group Ops")).toBeTruthy();
    expect(screen.getByText(/only its admins and the people and groups below/)).toBeTruthy();
    fireEvent.change(screen.getByTestId("project-member-who"), { target: { value: "lee@example.com" } });
    fireEvent.change(screen.getByLabelText("role"), { target: { value: "reviewer" } });
    await act(async () => { fireEvent.click(screen.getByText("Add")); });
    fireEvent.change(screen.getByLabelText("role of group Ops"), { target: { value: "builder" } });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { fireEvent.click(screen.getAllByText("Remove")[0]); });
    expect(sent).toEqual([
      { path: "/projects/prj_a/members", method: "PUT", json: { email: "lee@example.com", role: "reviewer" } },
      { path: "/projects/prj_a/members", method: "PUT", json: { group: "Ops", role: "builder" } },
      { path: "/projects/prj_a/members/pmb_1", method: "DELETE", json: undefined },
    ]);
  });

  it("is read-only for everyone but the project's admins, and says when only admins see the project", async () => {
    admin = false;
    show();
    expect(await screen.findByTestId("no-project-members")).toBeTruthy();
    expect(screen.getByText(/only organisation admins see this project/)).toBeTruthy();
    expect(screen.queryByText("Add")).toBeNull();
  });
});
