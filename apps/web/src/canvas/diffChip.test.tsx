// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });

const { useEditor } = await import("../editor/store");
const { useUi } = await import("../editor/ui");
const { Canvas } = await import("./Canvas");

afterEach(cleanup);

describe("version comparison on the canvas", () => {
  it("says which version the colours compare with, and clears them", () => {
    useEditor.setState({ wf: triage(), drill: [], layout: {}, diagnostics: [], selected: null });
    render(<QueryClientProvider client={new QueryClient()}><ReactFlowProvider><Canvas /></ReactFlowProvider></QueryClientProvider>);
    expect(screen.queryByTestId("diff-chip")).toBeNull();
    act(() => useUi.getState().setDiff({ against: "1.2.0", added: ["n_cls"], changed: [] }));
    expect(screen.getByTestId("diff-chip").textContent).toContain("Comparing with v1.2.0");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(useUi.getState().diff).toBeNull();
    expect(screen.queryByTestId("diff-chip")).toBeNull();
  });
});
