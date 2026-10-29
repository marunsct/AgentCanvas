// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const tree = {
  thread_id: "th1", workflow_id: "wf1",
  runs: [{
    id: "r:1", kind: "run", label: "Run 1", status: "success", start_ms: 0, duration_ms: 100, tokens: 15,
    children: [{
      id: "r:2", kind: "step", label: "Planner", canvas_node_id: "n_plan", status: "ok", start_ms: 10, duration_ms: 50, tokens: 15,
      children: [{
        id: "r:3", kind: "agent", label: "Helper agent researcher", start_ms: 20,
        children: [{ id: "r:4", kind: "tool", label: "Tool web_search", start_ms: 30, detail: { args: { q: "x" } } }],
      }],
    }],
  }],
};
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(async () => tree) }));

const { TrajectoryView } = await import("./TrajectoryView");

afterEach(cleanup);

describe("TrajectoryView (G-10)", () => {
  it("shows the steps of a conversation and opens a step down to its helper agent's tool call", async () => {
    const picked: string[] = [];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><TrajectoryView threadId="th1" onPick={(id) => picked.push(id)} /></QueryClientProvider>);
    expect(await screen.findByText("Planner")).toBeTruthy();
    expect(screen.queryByText("Helper agent researcher")).toBeNull(); // steps start closed
    fireEvent.click(screen.getByRole("button", { name: "open Planner" }));
    expect(screen.getByText("Helper agent researcher")).toBeTruthy();
    expect(screen.getByText("Tool web_search")).toBeTruthy(); // helpers start open
    fireEvent.click(screen.getByRole("button", { name: "open Tool web_search" }));
    expect(screen.getByText(/"q": "x"/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Planner" })); // the label: show it on the canvas
    expect(picked).toEqual(["n_plan"]);
  });
});
