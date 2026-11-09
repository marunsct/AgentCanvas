// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const history = () => [
  { checkpoint_id: "cp2", parent_checkpoint_id: "cp1", step: 1, source: "loop", next: [], writes: null, created_at: null, values: { topic: "b" } },
  { checkpoint_id: "cp1", parent_checkpoint_id: null, step: 0, source: "input", next: ["n1"], writes: null, created_at: null, values: { topic: "a" } },
];
const apiFetch = vi.fn(async (path: string) => (path.includes("/history") ? history() : []));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: (p: string) => apiFetch(p) }));

const { Timeline } = await import("./Timeline");
const { useRun } = await import("./runStore");

afterEach(cleanup);

describe("Timeline", () => {
  it("keeps the state being edited when the history is fetched again", async () => {
    useRun.setState({ threadId: "t1", runSeq: 0, running: false, starting: false });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><Timeline /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("option", { name: /0/ }));
    const box = screen.getByLabelText("checkpoint state") as HTMLTextAreaElement;
    expect(JSON.parse(box.value)).toEqual({ topic: "a" });
    fireEvent.change(box, { target: { value: '{"topic": "edited"}' } });
    const calls = apiFetch.mock.calls.length;
    act(() => useRun.setState((s) => ({ runSeq: s.runSeq + 1 }))); // another run ended: the history refetches
    await waitFor(() => expect(apiFetch.mock.calls.length).toBeGreaterThan(calls));
    await new Promise((r) => setTimeout(r, 20));
    expect((screen.getByLabelText("checkpoint state") as HTMLTextAreaElement).value).toBe('{"topic": "edited"}');
  });
});
