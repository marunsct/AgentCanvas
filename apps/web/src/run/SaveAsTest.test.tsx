// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalize } from "../ir/model";

const apiFetch = vi.fn();
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: (...a: unknown[]) => apiFetch(...a) }));
vi.mock("../ui/dialog", () => ({ promptDialog: vi.fn(async () => "greets") }));

const { SaveAsTest } = await import("./RunPanel");
const { useRun } = await import("./runStore");
const { useEditor } = await import("../editor/store");

afterEach(cleanup);

const mount = () => {
  useEditor.setState({ wf: normalize({ id: "wf_1", name: "w", state: { channels: [] } }), pins: {}, mocks: {} });
  useRun.setState({ running: false, input: { q: "hi" }, finalOutput: { a: 1 }, error: null, interrupts: [], route: [] });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><SaveAsTest /></QueryClientProvider>);
};

describe("Save as test case", () => {
  it("says when saving failed", async () => {
    apiFetch.mockRejectedValue(new Error("name taken"));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Save as test case" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Could not save the test case: name taken/);
  });

  it("saves once on a double-click", async () => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({});
    mount();
    const button = screen.getByRole("button", { name: "Save as test case" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
