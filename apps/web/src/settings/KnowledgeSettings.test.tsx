// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const can = vi.hoisted(() => ({ builder: true }));
const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => can.builder, canOrg: () => can.builder, useCan: () => can.builder, useCanOrg: () => can.builder, ME: { role: "builder" } }));
apiFetch.mockImplementation(async (_path: string, init?: { method?: string; json?: { chunking: unknown } }) =>
  init?.method === "PUT" ? init.json : { chunking: { strategy: "markdown", size: 800, overlap: 100 } });

const { KnowledgeSettings } = await import("./KnowledgeSettings");

afterEach(() => { cleanup(); apiFetch.mockClear(); can.builder = true; });

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><KnowledgeSettings /></QueryClientProvider>);
}

describe("Settings › Knowledge", () => {
  it("saves the chunking new knowledge bases start with, showing overlap only for fixed windows", async () => {
    renderIt();
    const strategy = await screen.findByLabelText("Split documents") as HTMLSelectElement;
    expect(strategy.value).toBe("markdown");
    expect(screen.queryByLabelText(/shared between passages/)).toBeNull();
    fireEvent.change(strategy, { target: { value: "fixed" } });
    fireEvent.change(screen.getByLabelText(/characters per passage/), { target: { value: "300" } });
    fireEvent.change(screen.getByLabelText(/shared between passages/), { target: { value: "30" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
    expect(apiFetch).toHaveBeenCalledWith(expect.stringMatching(/\/knowledge-defaults$/), {
      method: "PUT", json: { chunking: { strategy: "fixed", size: 300, overlap: 30 } },
    });
  });

  it("does not offer Save for a size the server would refuse", async () => {
    renderIt();
    fireEvent.change(await screen.findByLabelText(/characters per passage/), { target: { value: "50" } });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("is read-only for someone who cannot build", async () => {
    can.builder = false;
    renderIt();
    expect((await screen.findByLabelText("Split documents") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });
});
