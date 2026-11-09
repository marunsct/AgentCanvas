// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const history = () => [
  { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", step: 1, source: "loop", next: [], writes: null, created_at: null },
  { checkpoint_id: "cp1", parent_checkpoint_id: null, step: 0, source: "input", next: ["n1"], writes: null, created_at: null },
];
const states: Record<string, Record<string, unknown>> = { cp1: { topic: "a" }, cp2: { topic: "b" } };
const apiFetch = vi.fn(async (path: string) => {
  if (path.includes("/history")) return history();
  const cp = /\/checkpoints\/(\w+)$/.exec(path)?.[1];
  return cp ? { checkpoint_id: cp, step: 0, next: [], values: states[cp] } : [];
});
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: (p: string) => apiFetch(p) }));

const { Timeline } = await import("./Timeline");
const { useRun } = await import("./runStore");

afterEach(cleanup);

describe("Timeline", () => {
  it("keeps the state being edited when the history is fetched again", async () => {
    useRun.setState({ threadId: "t1", runSeq: 0, running: false, starting: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Timeline /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("option", { name: /^0/ }));
    const box = screen.getByLabelText("checkpoint state") as HTMLTextAreaElement;
    await waitFor(() => expect(JSON.parse(box.value)).toEqual({ topic: "a" }));
    fireEvent.change(box, { target: { value: '{"topic": "edited"}' } });
    const calls = apiFetch.mock.calls.length;
    act(() => useRun.setState((s) => ({ runSeq: s.runSeq + 1 }))); // another run ended: the history refetches
    await waitFor(() => expect(apiFetch.mock.calls.length).toBeGreaterThan(calls));
    await new Promise((r) => setTimeout(r, 20));
    expect((screen.getByLabelText("checkpoint state") as HTMLTextAreaElement).value).toBe('{"topic": "edited"}');
  });

  it("lists checkpoints without their state and reads only the one picked", async () => {
    apiFetch.mockClear();
    useRun.setState({ threadId: "t1", runSeq: 0, running: false, starting: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Timeline /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("option", { name: /^1/ }));
    await waitFor(() => expect(JSON.parse((screen.getByLabelText("checkpoint state") as HTMLTextAreaElement).value)).toEqual({ topic: "b" }));
    const paths = apiFetch.mock.calls.map(([p]) => p);
    expect(paths.filter((p) => p.includes("/history"))).toEqual(["/threads/t1/history?limit=200"]);
    expect(paths.filter((p) => p.includes("/checkpoints/"))).toEqual(["/threads/t1/checkpoints/cp2"]);
  });

  it("numbers checkpoints by their step and says when older ones are not shown", async () => {
    const many = Array.from({ length: 200 }, (_, i) => ({
      checkpoint_id: `c${i}`, parent_checkpoint_id: null, step: 340 - i, source: "loop", next: [], writes: null, created_at: null,
    }));
    apiFetch.mockImplementation(async (path: string) => (path.includes("/history") ? many : []));
    useRun.setState({ threadId: "t2", runSeq: 0, running: false, starting: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Timeline /></QueryClientProvider>);
    const ticks = await screen.findAllByRole("option");
    expect(ticks[0].textContent).toContain("141");
    expect(screen.getByText(/last 200 checkpoints/)).toBeTruthy();
  });
});
