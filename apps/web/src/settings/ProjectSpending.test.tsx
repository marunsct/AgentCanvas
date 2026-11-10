// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const sent: unknown[] = [];
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (_path: string, init?: { method?: string; json?: unknown }) => {
    if (init?.method === "PUT") sent.push(init.json);
    return {
      own: { max_cost_usd_day: 5, max_cost_usd_month: null },
      organisation: { max_cost_usd_day: null, max_cost_usd_month: 100 },
      day: { period: "day", period_start: "2026-11-10", limit_usd: 5, spent_usd: 1.5 },
      month: { period: "month", period_start: "2026-11", limit_usd: null, spent_usd: 12 },
    };
  }),
}));
vi.mock("../api/queries", () => ({ PROJECT_ID: "prj_a", PROJECT_NAME: "Team A" }));
vi.mock("../ui/dialog", () => ({ toast: () => {} }));

const { ProjectSpending } = await import("./ProjectSpending");

afterEach(() => { cleanup(); sent.length = 0; });

describe("ProjectSpending (E-10)", () => {
  it("shows the project's caps and spending with the organisation's cap, and saves an emptied field as no cap", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ProjectSpending /></QueryClientProvider>);
    const day = await screen.findByLabelText("Cap a day (USD)") as HTMLInputElement;
    expect(day.value).toBe("5");
    expect(screen.getByTestId("project-spent-month").textContent).toContain("the organisation's cap: $100.00");
    fireEvent.change(day, { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Cap a month (USD)"), { target: { value: "40" } });
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect(sent).toEqual([{ max_cost_usd_day: null, max_cost_usd_month: 40 }]);
  });
});
