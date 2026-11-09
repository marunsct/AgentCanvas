// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const item = (id: string) => ({
  id, kind: "approval", payload: { value: 1 }, workflow_id: "wf_1", workflow_name: "W", thread_id: "t", created_at: new Date().toISOString(),
});
const decisions: string[] = [];
let listed: Record<string, unknown>[] | null = null;
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn(async (path: string) => {
    if (path === "/inbox") return listed ?? [item("a"), item("b")];
    const id = /\/inbox\/(\w+)\/decision/.exec(path)?.[1] ?? "";
    decisions.push(id);
    if (id === "b") throw new Error("already decided by someone else");
    return {};
  }),
}));

const { InboxPage } = await import("./InboxPage");

afterEach(cleanup);

describe("Inbox", () => {
  it("removes a decided item at once, decides it once, and shows a failure on its own item", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><InboxPage /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getAllByTestId("inbox-item")).toHaveLength(2));
    const [a, b] = screen.getAllByTestId("inbox-item");
    const approveA = within(a).getByRole("button", { name: "Approve" });
    fireEvent.click(approveA);
    fireEvent.click(approveA);
    await waitFor(() => expect(screen.getAllByTestId("inbox-item")).toHaveLength(1)); // it stayed, clickable again
    expect(decisions).toEqual(["a"]);
    fireEvent.click(within(b).getByRole("button", { name: "Approve" }));
    const alert = await within(screen.getByTestId("inbox-item")).findByRole("alert");
    expect(alert.textContent).toContain("already decided by someone else");
    expect((within(screen.getByTestId("inbox-item")).getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("says who decides an item and keeps the buttons off for someone it does not name", async () => {
    listed = [{ ...item("c"), deciders: "admin role; group Finance", may_decide: false,
      why_not: "this step may be decided only by: admin role; group Finance" }];
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
      render(<QueryClientProvider client={qc}><MemoryRouter><InboxPage /></MemoryRouter></QueryClientProvider>);
      expect((await screen.findByTestId("inbox-deciders")).textContent).toBe("Decided by: admin role; group Finance");
      expect(screen.getByTestId("inbox-why-not").textContent).toContain("may be decided only by");
      expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
    } finally {
      listed = null;
    }
  });
});
