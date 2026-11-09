// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SpendingCapsOut } from "./SpendingCaps";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));

const capUse = (period: "day" | "month", limit: number | null, spent: number) =>
  ({ period, period_start: period === "day" ? "2026-11-10" : "2026-11", limit_usd: limit, spent_usd: spent });
const CAPS: SpendingCapsOut = {
  warn_percent: 80,
  server: { max_cost_usd_day: 0, max_cost_usd_month: 500 },
  organisation: { id: null, name: "Acme", own: { max_cost_usd_day: 10, max_cost_usd_month: null },
    day: capUse("day", 10, 10.5), month: capUse("month", 500, 42) },
  projects: [{ id: "prj_default", name: "Support", own: { max_cost_usd_month: 100 }, day: capUse("day", null, 2), month: capUse("month", 100, 30) }],
  alerts: [{ project_id: null, period: "day", period_start: "2026-11-10", share: 1, spent_usd: 10.5, limit_usd: 10,
    message: "The organisation reached its day spending cap: 10.5 of 10 USD", created_at: "2026-11-10T12:00:00Z" }],
};
apiFetch.mockImplementation(async (path: string) => (path === "/org/spending/caps" ? CAPS : {}));

const { SpendingCaps } = await import("./SpendingCaps");

afterEach(() => { cleanup(); apiFetch.mockClear(); });

function show() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SpendingCaps /></QueryClientProvider>);
}

describe("Settings › Spending › Spending caps", () => {
  it("shows each cap, what is used of it and the latest alerts", async () => {
    show();
    const day = await screen.findByTestId("spent-day-org");
    expect(day.textContent).toContain("$10.50 of $10.00 (105%)");
    expect(day.className).toBe("error-text"); // reached
    expect(screen.getByTestId("spent-month-org").textContent).toContain("$42.00 of $500.00 (8%)");
    expect(screen.getByTestId("spent-day-prj_default").textContent).toBe("$2.00 (no cap)");
    const month = screen.getByLabelText("Organisation: cap a month") as HTMLInputElement;
    expect(month.value).toBe("");
    expect(month.placeholder).toBe("server: 500"); // the server's cap applies, and is the most it may be
    expect(month.max).toBe("500");
    expect((screen.getByLabelText("Support: cap a month") as HTMLInputElement).value).toBe("100");
    expect(within(screen.getByTestId("cap-alerts")).getByText(/reached its day spending cap/)).toBeTruthy();
  });

  it("saves the organisation's caps as its limits and a project's on the project", async () => {
    show();
    const orgDay = await screen.findByLabelText("Organisation: cap a day");
    fireEvent.change(orgDay, { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("Alert at percent of a cap"), { target: { value: "90" } });
    const rows = within(screen.getByTestId("spend-caps")).getAllByRole("row");
    await act(async () => { fireEvent.click(within(rows[1]).getByRole("button", { name: "Save" })); });
    expect(apiFetch).toHaveBeenCalledWith("/org/settings", {
      method: "PUT", json: { limits: { max_cost_usd_day: 25, max_cost_usd_month: null, cost_warn_percent: 90 } },
    });
    fireEvent.change(screen.getByLabelText("Support: cap a month"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Support: cap a day"), { target: { value: "5" } });
    await act(async () => { fireEvent.click(within(rows[2]).getByRole("button", { name: "Save" })); });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/projects/prj_default/spending-caps", {
      method: "PUT", json: { max_cost_usd_day: 5, max_cost_usd_month: null },
    }));
  });
});
