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
let withdrawn: Record<string, unknown>[] = [];
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn(async (path: string) => {
    if (path === "/inbox") return listed ?? [item("a"), item("b")];
    if (path.startsWith("/inbox?status=withdrawn")) return withdrawn;
    if (path.startsWith("/runs/")) {
      decisions.push(path);
      return { cancelled: true, pending: false };
    }
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

  it("shows a run at a Wait step as waiting, with no decision to make, and stops it", async () => {
    listed = [{ ...item("w"), kind: "timer", run_id: "run_w", may_decide: false, why_not: "it continues on its own",
      payload: { kind: "timer", due_at: "2030-01-02T09:00:00+00:00", event: "invoice.paid", key: "INV-7" } }];
    decisions.length = 0;
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
      render(<QueryClientProvider client={qc}><MemoryRouter><InboxPage /></MemoryRouter></QueryClientProvider>);
      const card = await screen.findByRole("group", { name: "Waiting" });
      expect(within(card).getByTestId("wait-for").textContent).toMatch(/^Waits for the event invoice\.paid with key INV-7, until .*2030.* at most\.$/);
      expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
      expect(screen.queryByTestId("inbox-why-not")).toBeNull();
      fireEvent.click(within(card).getByRole("button", { name: "■ Stop waiting" }));
      await waitFor(() => expect(screen.queryAllByTestId("inbox-item")).toHaveLength(0));
      expect(decisions).toEqual(["/runs/run_w/cancel"]);
    } finally {
      listed = null;
    }
  });

  it("shows the last week's withdrawn items apart, with why, and says so of one a notification links to", async () => {
    const day = 24 * 3600 * 1000;
    const at = (ago: number) => new Date(Date.now() - ago).toISOString();
    listed = [item("a")];
    withdrawn = [{ ...item("w1"), status: "withdrawn", node_label: "Sign A", decided_at: at(day) },
      { ...item("old"), status: "withdrawn", decided_at: at(9 * day) }];
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
      render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/inbox?item=w1"]}><InboxPage /></MemoryRouter></QueryClientProvider>);
      const section = await screen.findByTestId("inbox-withdrawn");
      expect(section.textContent).toContain("moved on past these questions");
      const items = within(section).getAllByTestId("inbox-withdrawn-item");
      expect(items).toHaveLength(1); // older than a week: not shown
      expect(items[0].textContent).toContain("Sign A");
      expect(items[0].getAttribute("aria-current")).toBe("true");
      expect(within(items[0]).queryByRole("button")).toBeNull(); // nothing to decide
      expect(screen.getAllByTestId("inbox-item")).toHaveLength(1); // the pending list is unchanged
      expect(screen.getByTestId("inbox-gone").textContent).toContain("withdrawn");
    } finally {
      listed = null;
      withdrawn = [];
    }
  });

  it("shows the item a notification links to, or says it is gone", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/inbox?item=b"]}><InboxPage /></MemoryRouter></QueryClientProvider>);
    await waitFor(() => expect(screen.getAllByTestId("inbox-item")).toHaveLength(2));
    expect(screen.getAllByTestId("inbox-item").map((el) => el.getAttribute("aria-current"))).toEqual([null, "true"]);
    expect(screen.queryByTestId("inbox-gone")).toBeNull();
    cleanup();
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } })}>
      <MemoryRouter initialEntries={["/inbox?item=zzz"]}><InboxPage /></MemoryRouter></QueryClientProvider>);
    expect((await screen.findByTestId("inbox-gone")).textContent).toContain("no longer waiting");
  });
});
