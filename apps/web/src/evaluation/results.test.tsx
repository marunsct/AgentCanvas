// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { ExperimentOut } from "../api/generated";

const apiFetch = vi.fn(async (_path: string) => [{ id: "r1" }]);
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: (p: string) => apiFetch(p), apiDownload: vi.fn() }));

const { statusClass, useResults } = await import("./EvaluationPage");

describe("experiment status", () => {
  it("is red only when it failed", () => {
    expect(["done", "running", "failed", "queued", "cancelled"].map(statusClass))
      .toEqual([" status-ok", " status-running", " status-error", "", ""]);
  });
});

const exp = (status: string, completed: number) =>
  ({ id: "exp_1", status, summary: [{ target: "t", label: "t", completed, examples: 10 }] }) as unknown as ExperimentOut;

describe("experiment results", () => {
  it("are not fetched again, nor blanked, on every progress tick of a running experiment", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    const { result, rerender } = renderHook(({ e }) => useResults(e), { wrapper, initialProps: { e: exp("running", 1) } });
    await waitFor(() => expect(result.current.data).toBeTruthy());
    for (let i = 2; i < 6; i++) rerender({ e: exp("running", i) }); // the list polled: one more done each time
    expect(result.current.data).toBeTruthy(); // it used to say "Loading results…" in between
    expect(apiFetch).toHaveBeenCalledTimes(1);
    rerender({ e: exp("done", 10) }); // finished: fetched once more, keeping the rows meanwhile
    expect(result.current.data).toBeTruthy();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
  });
});
