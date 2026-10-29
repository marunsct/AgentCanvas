// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

const posts: string[] = [];
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string }) => {
    if (init?.method === "POST") {
      posts.push(path);
      return new Promise(() => undefined); // the round trip that used to leave the buttons enabled
    }
    if (path.includes("/workflows")) return { items: [] };
    if (path.includes("/setup")) return { done: true, default_model: null };
    return [];
  }),
}));

const { HomePage } = await import("./HomePage");

afterEach(() => { cleanup(); posts.length = 0; });

function renderHome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><HomePage /></MemoryRouter></QueryClientProvider>);
}

describe("HomePage (W-4)", () => {
  it("creates one workflow on a double-click of Create empty", async () => {
    renderHome();
    const button = screen.getByRole("button", { name: "Create empty" });
    await act(async () => { fireEvent.click(button); await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { fireEvent.click(button); }); // the second click of a double-click, a moment later
    expect(posts).toHaveLength(1);
  });

  it("starts one guided build on a double-click", async () => {
    renderHome();
    const button = screen.getByTestId("start-tour");
    await act(async () => { fireEvent.click(button); await new Promise((r) => setTimeout(r, 0)); });
    await act(async () => { fireEvent.click(button); }); // the second click of a double-click, a moment later
    expect(posts).toHaveLength(1);
  });
});
