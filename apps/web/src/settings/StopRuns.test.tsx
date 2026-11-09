// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const admin = vi.hoisted(() => ({ is: true }));
const apiFetch = vi.hoisted(() => vi.fn());
const dialog = vi.hoisted(() => ({ promptDialog: vi.fn(), toast: vi.fn() }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => admin.is, useCan: () => admin.is, ME: { role: "admin" } }));
vi.mock("../ui/dialog", () => dialog);

const { StopRuns } = await import("./StopRuns");

afterEach(() => { cleanup(); apiFetch.mockReset(); dialog.promptDialog.mockReset(); dialog.toast.mockReset(); admin.is = true; });

function renderIt() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><StopRuns /></QueryClientProvider>);
}

describe("Settings › Organisation › Stop all runs", () => {
  it("asks for a reason, stops the runs and says how many", async () => {
    dialog.promptDialog.mockResolvedValue("  agent looping  ");
    apiFetch.mockResolvedValue({ stopping: ["run_a", "run_b"], cancelled: ["run_c"] });
    renderIt();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Stop all runs…" })); });
    expect(dialog.promptDialog.mock.calls[0][0]).toMatchObject({ danger: true, input: { required: true } });
    expect(apiFetch).toHaveBeenCalledWith("/org/runs/stop", { method: "POST", json: { reason: "agent looping" } });
    expect(dialog.toast).toHaveBeenCalledWith("Stopping 3 runs");
  });

  it("does nothing when cancelled", async () => {
    dialog.promptDialog.mockResolvedValue(null);
    renderIt();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Stop all runs…" })); });
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("is not shown to someone who is not an admin", () => {
    admin.is = false;
    renderIt();
    expect(screen.queryByRole("button", { name: "Stop all runs…" })).toBeNull();
  });
});
