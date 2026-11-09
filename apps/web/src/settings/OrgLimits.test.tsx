// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const admin = vi.hoisted(() => ({ is: true }));
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => admin.is, useCan: () => admin.is, ME: { role: "admin" } }));

const none = { allow_local_mcp: null, rate_limit_per_deployment: null, rate_limit_per_webhook: null, rate_limit_per_project: null,
  rate_limit_per_share_client: null, max_concurrent_runs: null, audit_retention_days: null, memory_retention_days: null };
const server = { allow_local_mcp: true, rate_limit_per_deployment: 120, rate_limit_per_webhook: 120, rate_limit_per_project: 600,
  rate_limit_per_share_client: 30, max_concurrent_runs: 0, audit_retention_days: 400, memory_retention_days: 0 };
apiFetch.mockImplementation(async (_path: string, init?: { method?: string; json?: { limits: Record<string, unknown> } }) => {
  const own = { ...none, ...(init?.json?.limits ?? {}) };
  return { limits: { own, server, effective: { ...server, ...Object.fromEntries(Object.entries(own).filter(([, v]) => v !== null)) } } };
});

const { OrgLimits } = await import("./OrgLimits");

afterEach(() => { cleanup(); apiFetch.mockClear(); admin.is = true; });

function renderIt() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><OrgLimits /></QueryClientProvider>);
}

describe("Settings › Organisation › Limits", () => {
  it("sends only what changed, and shows what applies afterwards", async () => {
    renderIt();
    const project = await screen.findByLabelText("Runs per project") as HTMLInputElement;
    expect(project.placeholder).toBe("like the server");
    expect(project.max).toBe("600"); // a rate limit may be lowered, not raised
    fireEvent.change(project, { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Local MCP servers"), { target: { value: "off" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save limits" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org/settings", {
      method: "PUT", json: { limits: { rate_limit_per_project: 50, allow_local_mcp: false } },
    });
    await waitFor(() => expect(screen.getByTestId("applies-rate_limit_per_project").textContent).toBe("50 a minute"));
    expect(screen.getByTestId("applies-allow_local_mcp").textContent).toBe("not allowed");
  });

  it("sets how many runs may be in flight at once", async () => {
    renderIt();
    expect((await screen.findByTestId("applies-max_concurrent_runs")).textContent).toBe("no limit");
    fireEvent.change(screen.getByLabelText("Runs at once"), { target: { value: "4" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save limits" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org/settings", { method: "PUT", json: { limits: { max_concurrent_runs: 4 } } });
    await waitFor(() => expect(screen.getByTestId("applies-max_concurrent_runs").textContent).toBe("4 at once"));
  });

  it("does not offer to switch local MCP on when the server forbids it", async () => {
    apiFetch.mockImplementationOnce(async () => ({ limits: { own: none, server: { ...server, allow_local_mcp: false }, effective: { ...server, allow_local_mcp: false } } }));
    renderIt();
    const select = await screen.findByLabelText("Local MCP servers") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.textContent).toContain("Not allowed by the server");
  });

  it("is read-only for someone who is not an admin", async () => {
    admin.is = false;
    renderIt();
    expect((await screen.findByLabelText("Runs per project") as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Save limits" })).toBeNull();
  });
});
