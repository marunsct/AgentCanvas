// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const result = {
  model: "builtin", indexed: 3, stale: 2,
  hits: [{ thread_id: "th1", run_id: "r1", workflow_id: "wf1", workflow_name: "Support", score: 0.91, snippet: "User: I want my refund", started_at: "2026-10-29T10:00:00Z" }],
};
const apiFetch = vi.fn(async (path: string) => (path.includes("/index") ? { indexed: 2 } : result));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));

const { RunSearch } = await import("./RunSearch");

afterEach(cleanup);

describe("RunSearch (G-10)", () => {
  it("asks the server, lists the conversations and offers to index older runs", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><RunSearch /></MemoryRouter></QueryClientProvider>);
    expect(apiFetch).not.toHaveBeenCalled(); // nothing is asked before the person does
    fireEvent.change(screen.getByLabelText("find conversations where"), { target: { value: "  refund  " } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("User: I want my refund")).toBeTruthy();
    expect(apiFetch.mock.calls[0][0]).toMatch(/\/projects\/prj_default\/runs\/search\?q=refund$/);
    expect(screen.getByRole("link", { name: "Support" }).getAttribute("href")).toBe("/workflows/wf1?thread=th1");
    expect(screen.getByText("91%")).toBeTruthy();
    expect(screen.getByText(/2 older runs are not searchable yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Index them" }));
    await vi.waitFor(() => expect(apiFetch.mock.calls.some(([p]) => String(p).endsWith("/runs/search/index"))).toBe(true));
  });
});
