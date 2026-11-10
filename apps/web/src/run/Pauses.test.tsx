// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, useCan: () => true, useCanOrg: () => true, ME: { role: "admin" } }));

const { Pauses } = await import("./RunPanel");
const { useRun } = await import("./runStore");

afterEach(() => { cleanup(); apiFetch.mockReset(); });

const pause = { id: "int_1", kind: "approval" as const, canvas_node_id: "n_ok", payload: { value: 1 } };

function mount() {
  useRun.setState({ running: false, starting: false, interrupts: [pause] });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Pauses workflowId="wf_1" /></QueryClientProvider>);
}

describe("the Run tab's pauses", () => {
  it("say who may decide them, as the Inbox does, and are off for a pause you may not decide", async () => {
    apiFetch.mockResolvedValue([{ id: "int_1", kind: "approval", deciders: "group Finance", may_decide: false,
      why_not: "this step may be decided only by: group Finance" }]);
    mount();
    expect((await screen.findByTestId("inbox-deciders")).textContent).toBe("Decided by: group Finance");
    expect(screen.getByTestId("inbox-why-not").textContent).toContain("only by: group Finance");
    expect(apiFetch).toHaveBeenCalledWith("/inbox?workflow_id=wf_1");
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("leave the buttons on for someone who may decide", async () => {
    apiFetch.mockResolvedValue([{ id: "int_1", kind: "approval", deciders: "admin role", may_decide: true, why_not: null }]);
    mount();
    expect((await screen.findByTestId("inbox-deciders")).textContent).toBe("Decided by: admin role");
    expect(screen.queryByTestId("inbox-why-not")).toBeNull();
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
