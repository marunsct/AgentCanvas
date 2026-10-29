// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const recrawl = { every_hours: 24, sitemaps: ["https://example.org/sitemap.xml"], last: { at: "2026-10-29T10:00:00Z", added: 0, updated: 1, removed: 0, unchanged: 1, failed: [] } };
const kb = { id: "kb1", name: "docs", description: "", documents: 2, chunks: 4, version: 1, stale: false, chunking: {}, embedding: {}, recrawl };
const docs = [
  { id: "d1", name: "Old import", source: "url", url: "not a url", chunks: 2, characters: 10, created_at: "" },
  { id: "d2", name: "Guide", source: "url", url: "https://example.org/guide", chunks: 2, characters: 10, created_at: "" },
];
const apiFetch = vi.fn(async (path: string, _init?: unknown) =>
  path.endsWith("/documents") ? docs : path.endsWith("/knowledge") ? [kb] : []);
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));
vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));

const { KnowledgePage } = await import("./KnowledgePage");

afterEach(cleanup);

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><KnowledgePage /></MemoryRouter></QueryClientProvider>);
}

describe("KnowledgePage (W-12)", () => {
  it("lists a document whose stored address is not a URL instead of failing the page", async () => {
    renderPage();
    expect(await screen.findByText("Old import")).toBeTruthy(); // `new URL` threw and took the page to the error boundary
    expect(screen.getByText("not a url").tagName).toBe("TD");
    expect(screen.getByRole("link", { name: "example.org" }).getAttribute("href")).toBe("https://example.org/guide");
  });

  it("shows the re-crawl schedule and last result, and changes or runs it", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><KnowledgePage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByTestId("kb-crawl-last")).toHaveProperty("textContent", "Last: 1 updated, 1 unchanged");
    expect(screen.getByText(/Sitemaps: https:\/\/example.org\/sitemap.xml/)).toBeTruthy();
    const select = screen.getByLabelText("re-crawl interval") as HTMLSelectElement;
    expect(select.value).toBe("24");
    fireEvent.change(select, { target: { value: "168" } });
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/knowledge/kb1/recrawl", { method: "PUT", json: { every_hours: 168 } }));
    fireEvent.click(screen.getByRole("button", { name: "Fetch now" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/knowledge/kb1/recrawl", { method: "POST" }));
  });

  it("renames a knowledge base and sets its description, warning that blocks name it", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Rename…" }));
    fireEvent.change(screen.getByLabelText("knowledge base name"), { target: { value: "HR Handbook" } });
    expect(screen.getByTestId("kb-rename-hint").textContent).toContain("Saved as hr-handbook");
    fireEvent.change(screen.getByLabelText("knowledge base description"), { target: { value: " Policies for staff " } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save" })); });
    expect(apiFetch).toHaveBeenCalledWith("/knowledge/kb1", { method: "PATCH", json: { name: "hr-handbook", description: "Policies for staff" } });
    expect(screen.queryByLabelText("knowledge base name")).toBeNull(); // the form closes once saved
  });
});
