// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const summary = (runs: number, cost: number) => ({
  runs, failed: 2, failure_rate: 0.6667, cost_usd: cost, tokens: 10, latency_p50_ms: 1500, latency_p95_ms: 4200,
  failure_groups: [{ reason: "Model said … (HTTP N)", runs: 2, workflows: ["Chat"], example: { run_id: "r1", thread_id: "th1", workflow_id: "wf1" } }],
  workflows: [{ id: "wf1", name: "Chat", runs: 3, failed: 2, cost_usd: 0.6 }],
  flagged: [{ run_id: "r2", thread_id: "th2", workflow_id: "wf1", workflow_name: "Chat", flags: 2, why: ["politeness", "user feedback"] }],
});
const digests = [
  { week_start: "2026-11-02T00:00:00Z", week_end: "2026-11-09T00:00:00Z", current: false, summary: summary(3, 0.6) },
  { week_start: "2026-10-26T00:00:00Z", week_end: "2026-11-02T00:00:00Z", current: false, summary: summary(2, 0.3) },
];
const now = { week_start: "2026-11-09T00:00:00Z", week_end: "2026-11-16T00:00:00Z", current: true, summary: summary(1, 0.1) };
const apiFetch = vi.fn(async (path: string) => (path.endsWith("/current") ? now : digests));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));

const { RunDigest, change, seconds, weekLabel } = await import("./RunDigest");

afterEach(cleanup);

describe("RunDigest (F-11)", () => {
  it("shows last week against the week before, and this week so far on request", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><RunDigest /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByTestId("run-digest")).toBeTruthy();
    expect(apiFetch.mock.calls.map(([p]) => p)).toEqual(["/projects/prj_default/run-digests?limit=2"]); // not the live one yet
    const text = screen.getByTestId("run-digest").textContent ?? "";
    expect(text).toContain("3 runs (+50% on the week before), 2 failed (67%), $0.60 spent (+100% on the week before)");
    expect(text).toContain("1.5 s typically, 4.2 s at the 95th percentile");
    expect(screen.getByLabelText("failure groups").textContent).toContain("2× Model said … (HTTP N)");
    expect(screen.getByRole("link", { name: "newest" }).getAttribute("href")).toBe("/workflows/wf1?thread=th1");
    expect(screen.getByLabelText("flagged runs").textContent).toContain("2 flags: politeness, user feedback");
    fireEvent.click(screen.getByRole("button", { name: "This week so far" }));
    expect(await screen.findByText(/This week so far \(since/, { selector: "strong" })).toBeTruthy();
    expect(apiFetch.mock.calls.some(([p]) => p === "/projects/prj_default/run-digests/current")).toBe(true);
  });

  it("formats weeks, changes and durations", () => {
    expect(weekLabel(digests[0])).toMatch(/^Week of 2 Nov – 8 Nov$|^Week of Nov 2 – Nov 8$/);
    expect(change(3, 2)).toBe(" (+50% on the week before)");
    expect(change(2, 2)).toBe(" (same as the week before)");
    expect(change(2, undefined)).toBe("");
    expect(change(2, 0)).toBe("");
    expect(seconds(null)).toBe("—");
    expect(seconds(800)).toBe("800 ms");
    expect(seconds(90_000)).toBe("2 min");
  });
});
