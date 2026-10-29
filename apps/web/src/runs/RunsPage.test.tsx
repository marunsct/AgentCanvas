// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { exampleRun, selectionProblem } from "./runs";

const thread = (id: string, workflow_id: string) => ({
  id, workflow_id, workflow_name: workflow_id, status: "idle", title: "", runs: 1, created_at: "", updated_at: "",
  last_started_at: "2026-10-29T10:00:00Z", last_ended_at: "2026-10-29T10:00:01Z",
});
const state = { threads: [thread("th1", "wf_a"), thread("th2", "wf_a"), thread("th3", "wf_b")] };
const runs: Record<string, { id: string; status: string }[]> = {
  th1: [{ id: "r1a", status: "success" }, { id: "r1b", status: "error" }], // the last good one is added
  th2: [{ id: "r2", status: "success" }],
  th3: [{ id: "r3", status: "success" }],
};
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));
apiFetch.mockImplementation(async (path: string) => {
  if (path.startsWith("/threads?")) return { items: state.threads, next_cursor: null };
  if (/^\/threads\/[^/]+\/runs$/.test(path)) return runs[path.split("/")[2]];
  if (path.endsWith("/datasets")) return [{ id: "ds1", name: "golden", examples: 3, version: 1, splits: {} }];
  if (path.includes("/workflows")) return { items: [] };
  return {};
});

const { RunsPage } = await import("./RunsPage");

afterEach(() => { cleanup(); apiFetch.mockClear(); });

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><RunsPage /></MemoryRouter></QueryClientProvider>);
}

describe("run helpers", () => {
  it("adds a conversation's last run that has a recording", () => {
    expect(exampleRun([{ id: "a", status: "success" }, { id: "b", status: "error" }])).toBe("a");
    expect(exampleRun([{ id: "a", status: "interrupted" }, { id: "b", status: "success" }])).toBe("b");
    expect(exampleRun([{ id: "a", status: "error" }, { id: "b", status: "cancelled" }])).toBeUndefined();
  });
  it("wants conversations of one workflow", () => {
    expect(selectionProblem([])).toMatch(/Pick/);
    expect(selectionProblem(["a", "b"])).toMatch(/different workflows/);
    expect(selectionProblem(["a", "a"])).toBeNull();
  });
});

describe("RunsPage: add runs as dataset examples", () => {
  it("sends the last finished run of each chosen conversation to the chosen dataset", async () => {
    renderPage();
    fireEvent.click(await screen.findByLabelText("select conversation th1"));
    fireEvent.click(screen.getByLabelText("select conversation th2"));
    await screen.findByRole("option", { name: /golden/ });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Add these runs as examples" })); });
    expect(apiFetch).toHaveBeenCalledWith("/datasets/ds1/examples/from-runs", { method: "POST", json: { run_ids: ["r1a", "r2"] } });
    expect(screen.queryByLabelText("add to a dataset")).toBeNull(); // the selection is cleared once added
  });

  it("says when the chosen conversations belong to different workflows", async () => {
    renderPage();
    fireEvent.click(await screen.findByLabelText("select conversation th1"));
    fireEvent.click(screen.getByLabelText("select conversation th3"));
    expect(screen.getByText(/different workflows/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add these runs as examples" })).toBeNull();
  });
});
