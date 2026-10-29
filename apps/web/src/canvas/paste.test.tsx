// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });

const { useEditor } = await import("../editor/store");
const { useCopilot } = await import("../copilot/copilot");
const { Canvas } = await import("./Canvas");

const dispatch = vi.fn();
const key = (k: string) => fireEvent.keyDown(document.body, { key: k, ctrlKey: true });

beforeEach(() => {
  dispatch.mockClear();
  useEditor.setState({ wf: triage(), dispatch, drill: [], layout: {}, diagnostics: [], selected: null, notice: null });
  useCopilot.setState({ ghost: null });
  const qc = new QueryClient();
  render(<QueryClientProvider client={qc}><ReactFlowProvider><Canvas /></ReactFlowProvider></QueryClientProvider>);
});
afterEach(cleanup);

describe("paste (W-11)", () => {
  it("pastes into the workflow shown at the top level", () => {
    act(() => useEditor.getState().focusNode?.("n_cls")); // select a block, copy it
    key("c");
    key("v");
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("does not paste into the top level while a subgraph or a Copilot proposal is shown", () => {
    act(() => useEditor.getState().focusNode?.("n_cls"));
    key("c");
    act(() => useCopilot.setState({ ghost: { workflow: triage(), diff: { added: [], changed: [], removed: [] } } as never }));
    key("v");
    const wf = triage();
    const inner = { id: "inner", name: "inner", state: { channels: [] }, nodes: [], edges: [] };
    wf.nodes.push({ id: "n_sub", type: "core.subgraph", name: "sub", config: { workflow: inner } });
    act(() => { useCopilot.setState({ ghost: null }); useEditor.setState({ wf, drill: ["n_sub"] }); });
    expect(useEditor.getState().drill).toEqual(["n_sub"]);
    key("v");
    expect(dispatch).not.toHaveBeenCalled(); // it pasted into the top level, out of sight
    expect(useEditor.getState().notice?.text).toMatch(/Paste/);
  });
});
