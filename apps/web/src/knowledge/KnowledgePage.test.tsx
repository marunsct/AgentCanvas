// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const kb = { id: "kb1", name: "docs", description: "", documents: 2, chunks: 4, version: 1, stale: false, chunking: {}, embedding: {} };
const docs = [
  { id: "d1", name: "Old import", source: "url", url: "not a url", chunks: 2, characters: 10, created_at: "" },
  { id: "d2", name: "Guide", source: "url", url: "https://example.org/guide", chunks: 2, characters: 10, created_at: "" },
];
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn(async (path: string) => (path.endsWith("/documents") ? docs : path.endsWith("/knowledge") ? [kb] : [])),
}));
vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));

const { KnowledgePage } = await import("./KnowledgePage");

afterEach(cleanup);

describe("KnowledgePage (W-12)", () => {
  it("lists a document whose stored address is not a URL instead of failing the page", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><KnowledgePage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("Old import")).toBeTruthy(); // `new URL` threw and took the page to the error boundary
    expect(screen.getByText("not a url").tagName).toBe("TD");
    expect(screen.getByRole("link", { name: "example.org" }).getAttribute("href")).toBe("https://example.org/guide");
  });
});
