// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
const apiDownload = vi.hoisted(() => vi.fn());
const dialog = vi.hoisted(() => ({ promptDialog: vi.fn(), confirmDialog: vi.fn(), toast: vi.fn() }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch, apiDownload }));
vi.mock("../api/auth", () => ({ maturaMode: () => false }));
vi.mock("../api/session", () => ({
  useCan: () => true, orgPortalUrl: () => null,
  useMe: (pick: (s: unknown) => unknown) => pick({ me: { id: "usr_me" } }),
}));
vi.mock("../ui/dialog", () => dialog);

const { MembersPage } = await import("./MembersPage");

afterEach(() => { cleanup(); apiFetch.mockReset(); apiDownload.mockReset(); for (const f of Object.values(dialog)) f.mockReset(); });

const ana = { user_id: "usr_ana", email: "ana@example.com", display_name: "Ana", role: "builder", invited: false, accepted: true, deactivated: false };

async function renderIt() {
  apiFetch.mockImplementation(async (path: string) => (path === "/org/members" ? [ana] : { account_erased: true }));
  await act(async () => {
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><MembersPage /></QueryClientProvider>);
  });
}

describe("Members: data-subject requests", () => {
  it("erases a member with the reason, and their conversations when asked", async () => {
    await renderIt();
    dialog.promptDialog.mockResolvedValue(" DSAR 42 ");
    dialog.confirmDialog.mockResolvedValue(true);
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Erase…" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org/members/usr_ana/erase", { method: "POST", json: { reason: "DSAR 42", conversations: true } });
    expect(dialog.toast).toHaveBeenCalledWith("Erased");
  });

  it("does nothing without a reason", async () => {
    await renderIt();
    dialog.promptDialog.mockResolvedValue(null);
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Erase…" })); });
    expect(apiFetch).not.toHaveBeenCalledWith(expect.stringContaining("/erase"), expect.anything());
  });

  it("downloads a member's data", async () => {
    await renderIt();
    await act(async () => { fireEvent.click(await screen.findByRole("button", { name: "Export data" })); });
    expect(apiDownload).toHaveBeenCalledWith("/org/members/usr_ana/export", "agentcanvas-usr_ana.json");
  });
});
