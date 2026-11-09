// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SpendingSettings, usd } from "./SpendingSettings";

const entry = (over: object) => ({ runs: 1, tokens_in: 1000, tokens_out: 500, cost_usd: 0, cache_hits: 0, fallbacks: 0, ...over });
const REPORT = {
  since: "2026-10-01T00:00:00Z", until: "2026-10-31T00:00:00Z",
  total: entry({ runs: 5, tokens_in: 4100, tokens_out: 2050, cost_usd: 4.25, cache_hits: 3, fallbacks: 1 }),
  by_project: [entry({ id: "prj_default", name: "Default project", cost_usd: 4 }), entry({ id: "prj_2", name: "Support bots", cost_usd: 0.25 })],
  by_workflow: [entry({ id: "wf_1", name: "Triage", project_id: "prj_default", cost_usd: 4, runs: 3 })],
  by_person: [entry({ id: "usr_a", name: "Ana", email: "ana@example.com", cost_usd: 2.25, cache_hits: 2 }),
    entry({ id: null, name: "No person (deployments, schedules, events)", email: null })],
  by_deployment: [entry({ id: "dep_1", name: "Triage (prod)", workflow_id: "wf_1", cost_usd: 3 }),
    entry({ id: null, name: "Not through a deployment (the editor, the Playground)", workflow_id: null, cost_usd: 1.25 })],
};

let urls: string[] = [];
beforeEach(() => {
  urls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    urls.push(String(url));
    return new Response(JSON.stringify(REPORT), { headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SpendingSettings /></QueryClientProvider>);
}

describe("Settings › Spending", () => {
  it("shows the totals and the cost by project, workflow and person", async () => {
    show();
    expect((await screen.findByTestId("spend-total")).textContent).toContain("$4.25");
    expect(screen.getByTestId("spend-total").textContent).toContain("6,150 tokens");
    expect(within(screen.getByTestId("spend-projects")).getByText("Support bots")).toBeTruthy();
    expect(within(screen.getByTestId("spend-workflows")).getByText("Triage")).toBeTruthy();
    const people = screen.getByTestId("spend-people");
    expect(within(people).getByText("Ana")).toBeTruthy();
    expect(within(people).getByText("ana@example.com")).toBeTruthy();
    expect(within(people).getByText(/No person/)).toBeTruthy();
    const deployments = screen.getByTestId("spend-deployments");
    expect(within(deployments).getByText("Triage (prod)")).toBeTruthy();
    expect(within(deployments).getByText(/Not through a deployment/)).toBeTruthy();
  });

  it("asks for the chosen dates, the whole last day included", async () => {
    show();
    await screen.findByTestId("spend-total");
    await act(async () => {
      fireEvent.change(screen.getByLabelText("from date"), { target: { value: "2026-10-01" } });
      fireEvent.change(screen.getByLabelText("to date"), { target: { value: "2026-10-31" } });
    });
    await screen.findByTestId("spend-total");
    const q = new URLSearchParams(urls[urls.length - 1].split("?")[1]);
    expect(new Date(q.get("since")!).getTime()).toBe(new Date("2026-10-01T00:00:00").getTime());
    expect(new Date(q.get("until")!).getTime()).toBe(new Date("2026-11-01T00:00:00").getTime());
    const before = urls.length;
    await act(async () => { fireEvent.change(screen.getByLabelText("from date"), { target: { value: "2026-12-01" } }); });
    expect(urls.length).toBe(before); // a start after the end asks for nothing
    expect(screen.getByRole("status").textContent).toContain("start date");
  });

  it("writes small amounts with enough digits", () => {
    expect([usd(0), usd(0.0012), usd(4.256), usd(12)]).toEqual(["$0.00", "$0.0012", "$4.26", "$12.00"]);
  });
});
