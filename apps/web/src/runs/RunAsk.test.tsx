// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const answer = {
  answer: "One failed refund run cost $0.25.", error: null, model: "anthropic:claude-haiku-4-5", cost_usd: 0.0012,
  searches: [{ status: "error", since: "2026-11-03T00:00:00+00:00", min_cost_usd: 0.1, text: "refund", matched: 1 }],
  runs: [{ run_id: "r1", thread_id: "th1", workflow_id: "wf1", workflow_name: "Support", status: "error",
    error: "Model said no", cost_usd: 0.25, started_at: "2026-11-08T10:00:00Z" }],
};
const apiFetch = vi.fn(async (..._args: unknown[]) => answer);
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));

const { RunAsk, searchText } = await import("./RunAsk");

afterEach(cleanup);

function show(workflowId?: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><RunAsk workflowId={workflowId} /></MemoryRouter></QueryClientProvider>);
}

describe("RunAsk (F-11)", () => {
  it("sends the question, shows the answer, the filters used and the runs found", async () => {
    show();
    expect(screen.getByRole("button", { name: "Ask" })).toHaveProperty("disabled", true);
    fireEvent.change(screen.getByLabelText("ask the Copilot about runs"), { target: { value: " failed refunds over 10 cents " } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("One failed refund run cost $0.25.")).toBeTruthy();
    const [path, init] = apiFetch.mock.calls[0] as [string, { method: string; json: unknown }];
    expect(path).toBe("/projects/prj_default/runs/ask");
    expect(init).toMatchObject({ method: "POST", json: { question: "failed refunds over 10 cents" } });
    expect(screen.getByText(/Searched: failed, since 2026-11-03 00:00, at least \$0.1, about "refund" \(1 matched\)/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Support" }).getAttribute("href")).toBe("/workflows/wf1?thread=th1");
    expect(screen.getByText("Model said no")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByTestId("run-ask-answer")).toBeNull();
  });

  it("keeps to the page's workflow filter", async () => {
    apiFetch.mockClear();
    show("wf9");
    fireEvent.change(screen.getByLabelText("ask the Copilot about runs"), { target: { value: "what failed?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByTestId("run-ask-answer");
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ json: { question: "what failed?", workflow_id: "wf9" } });
  });

  it("says a search without filters is every run", () => {
    expect(searchText({ matched: 4 })).toBe("every run (4 matched)");
    expect(searchText({ label: "tier=gold", workflow: "wf1" })).toBe("workflow wf1, label tier=gold");
  });
});
