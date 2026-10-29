// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fineTuneBody, NEW_FINE_TUNE } from "./FineTune";

const { apiFetch, apiDownload } = vi.hoisted(() => ({
  apiFetch: vi.fn(async (path: string, _init?: { json?: Record<string, unknown> }): Promise<unknown> =>
    path.startsWith("/workflows/wf1/datasets") ? [{ id: "ds1", name: "Gold", version: 2, examples: 5 }]
      : { examples: 2, skipped: [{ ref: "run_9", reason: "it got a thumbs-down" }] }),
  apiDownload: vi.fn(async (..._args: unknown[]): Promise<void> => undefined),
}));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch, apiDownload }));

const { FineTune } = await import("./FineTune");

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("fine-tuning export", () => {
  it("builds the request: runs by score, or a dataset with its split, and the system message", () => {
    expect(fineTuneBody(NEW_FINE_TUNE)).toMatchObject({ source: "runs", format: "openai", min_score: 0.8, dataset_id: null, system: null });
    const d = { ...NEW_FINE_TUNE, source: "dataset" as const, split: " train ", system: "-", min_score: "7" };
    expect(fineTuneBody(d, "ds1")).toMatchObject({ source: "dataset", dataset_id: "ds1", split: "train", system: "", min_score: 1 });
  });

  it("previews what would be exported and downloads the file with the same options", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><FineTune wfId="wf1" /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("minimum score"), { target: { value: "0.9" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/it got a thumbs-down/)).toBeTruthy();
    const call = apiFetch.mock.calls.find(([p]) => p.includes("preview=true"));
    expect(call?.[0]).toBe("/workflows/wf1/finetune-export?preview=true");
    expect(call?.[1]?.json).toMatchObject({ source: "runs", min_score: 0.9 });
    fireEvent.click(screen.getByRole("button", { name: "Download JSONL" }));
    await vi.waitFor(() => expect(apiDownload).toHaveBeenCalled());
    expect(apiDownload.mock.calls[0]).toMatchObject(["/workflows/wf1/finetune-export", "finetune.jsonl", { min_score: 0.9 }]);
  });
});
