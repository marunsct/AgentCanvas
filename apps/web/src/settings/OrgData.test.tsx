// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const admin = vi.hoisted(() => ({ is: true }));
const apiFetch = vi.hoisted(() => vi.fn());
const apiDownload = vi.hoisted(() => vi.fn());
const dialog = vi.hoisted(() => ({ promptDialog: vi.fn(), confirmDialog: vi.fn(), toast: vi.fn() }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch, apiDownload }));
vi.mock("../api/session", () => ({
  useCan: () => admin.is, useCanOrg: () => admin.is,
  useMe: (pick: (s: unknown) => unknown) => pick({ me: { org_id: "org_a", org_names: { org_a: "Acme" } } }),
}));
vi.mock("../ui/dialog", () => dialog);

const { MyData, OrgData } = await import("./OrgData");

afterEach(() => {
  cleanup(); apiFetch.mockReset(); apiDownload.mockReset(); admin.is = true;
  for (const f of Object.values(dialog)) f.mockReset();
});

function answers(routes: Record<string, unknown>) {
  apiFetch.mockImplementation(async (path: string, init?: { method?: string }) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    if (!(key in routes)) throw new Error(`unexpected ${key}`);
    return routes[key];
  });
}

async function renderIt(node: React.ReactNode) {
  await act(async () => {
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}>{node}</QueryClientProvider>);
  });
}

const ready = { id: "oex_1", status: "ready", created_at: "2026-11-10T10:00:00Z", finished_at: null, expires_at: "2026-11-11T10:00:00Z", size: 2097152, sha256: "x", error: null };

describe("Settings › Organisation › the organisation's data", () => {
  it("downloads a built export through its one-time link", async () => {
    answers({ "GET /org/export": [ready], "GET /org/deletion": { scheduled: false }, "POST /org/export/oex_1/link": { path: "/v1/exports/acx_t" } });
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { assign }, configurable: true });
    await renderIt(<OrgData />);
    expect((await screen.findByTestId("export-status")).textContent).toContain("2.0 MB");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Download" })); });
    expect(assign).toHaveBeenCalledWith("/api/v1/exports/acx_t");
  });

  it("starts an export and says it is being built", async () => {
    answers({ "GET /org/export": [{ ...ready, status: "building" }], "GET /org/deletion": { scheduled: false } });
    await renderIt(<OrgData />);
    expect((await screen.findByTestId("export-status")).textContent).toContain("Building");
    expect((screen.getByRole("button", { name: "Export everything" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("deletes only after the name is typed exactly and a reason is given", async () => {
    answers({ "GET /org/export": [], "GET /org/deletion": { scheduled: false }, "DELETE /org": { scheduled: true, delete_after: "2026-11-17T10:00:00Z" } });
    await renderIt(<OrgData />);
    dialog.promptDialog.mockResolvedValueOnce("acme");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Delete the organisation…" })); });
    expect(apiFetch).not.toHaveBeenCalledWith("/org", expect.anything());
    dialog.promptDialog.mockResolvedValueOnce("Acme").mockResolvedValueOnce(" contract ended ");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Delete the organisation…" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org", { method: "DELETE", json: { confirm: "Acme", reason: "contract ended" } });
    expect(await screen.findByTestId("deletion-scheduled")).toBeTruthy();
  });

  it("takes a scheduled deletion back", async () => {
    answers({ "GET /org/export": [], "GET /org/deletion": { scheduled: true, delete_after: "2026-11-17T10:00:00Z" }, "DELETE /org/deletion": { scheduled: false } });
    dialog.confirmDialog.mockResolvedValue(true);
    await renderIt(<OrgData />);
    const back = await screen.findByRole("button", { name: "Take the deletion back" });
    await act(async () => { fireEvent.click(back); });
    expect(apiFetch).toHaveBeenCalledWith("/org/deletion", { method: "DELETE" });
    expect(await screen.findByRole("button", { name: "Delete the organisation…" })).toBeTruthy();
  });

  it("is not shown to someone who is not an admin", async () => {
    admin.is = false;
    await renderIt(<OrgData />);
    expect(screen.queryByRole("button", { name: "Export everything" })).toBeNull();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});

describe("Settings › You › Your data", () => {
  it("downloads your own data", async () => {
    apiDownload.mockResolvedValue(undefined);
    await renderIt(<MyData />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Download my data" })); });
    expect(apiDownload).toHaveBeenCalledWith("/me/export", "agentcanvas-my-data.json");
  });
});
