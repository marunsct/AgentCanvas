// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, useCan: () => true, useCanOrg: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { Inspector } = await import("./Inspector");
const { StateDesigner, renameStateRefs } = await import("./StateDesigner");

const dispatch = vi.fn();
beforeEach(() => { dispatch.mockClear(); useEditor.setState({ wf: triage(), dispatch, selected: null, diagnostics: [] }); });
afterEach(cleanup);
const withQueries = (ui: ReactNode) => <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>;

describe("names (W-10)", () => {
  it("says why a block name cannot be used instead of dropping it", () => {
    useEditor.setState({ selected: "n_cls" });
    render(withQueries(<Inspector />));
    const name = screen.getByLabelText("node name") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Bad Name" } });
    fireEvent.blur(name);
    expect(screen.getByRole("alert").textContent).toMatch(/lowercase letters/);
    expect(name.value).toBe("Bad Name"); // kept for fixing
    fireEvent.change(name, { target: { value: "escalate" } }); // another block's name: names become code identifiers
    fireEvent.blur(name);
    expect(screen.getByRole("alert").textContent).toMatch(/already called “escalate”/);
    expect(dispatch).not.toHaveBeenCalled();
    fireEvent.change(name, { target: { value: "sorter" } });
    fireEvent.blur(name);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(dispatch).toHaveBeenCalledWith([{ op: "UpdateNode", id: "n_cls", patch: { name: "sorter" } }]);
  });

  it("follows undo and redo: the name and label shown are the block's current ones", () => {
    useEditor.setState({ dispatch: useEditor.getInitialState().dispatch, selected: "n_cls" });
    render(withQueries(<Inspector />));
    const name = () => screen.getByLabelText("node name") as HTMLInputElement;
    const label = () => screen.getByLabelText("node label") as HTMLInputElement;
    fireEvent.change(name(), { target: { value: "sorter" } });
    fireEvent.blur(name());
    fireEvent.change(label(), { target: { value: "The sorter" } });
    fireEvent.blur(label());
    expect(name().value).toBe("sorter");
    expect(label().value).toBe("The sorter");
    const patch = (p: Record<string, unknown>) => act(() => useEditor.getState().dispatch([{ op: "UpdateNode", id: "n_cls", patch: p }])); // as undo would
    patch({ name: "classify", label: null });
    expect(name().value).toBe("classify"); // the field used to keep what was typed
    expect(label().value).toBe("");
    patch({ label: "The sorter" }); // and redo
    expect(label().value).toBe("The sorter");
  });

  it("says why a channel name cannot be used", () => {
    render(<StateDesigner />);
    const [first, second] = screen.getAllByLabelText("channel name") as HTMLInputElement[];
    fireEvent.change(second, { target: { value: first.value } });
    fireEvent.blur(second);
    expect(screen.getByRole("alert").textContent).toMatch(/Another channel is already called/);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("offers to update the blocks that still refer to a renamed channel", () => {
    useEditor.setState({ dispatch: useEditor.getInitialState().dispatch }); // the real one: the rename applies locally
    render(<StateDesigner />);
    const urgency = (screen.getAllByLabelText("channel name") as HTMLInputElement[]).find((i) => i.value === "urgency")!;
    fireEvent.change(urgency, { target: { value: "priority" } });
    fireEvent.blur(urgency);
    expect(screen.getByTestId("rename-refs").textContent).toMatch(/still use state\.urgency/);
    fireEvent.click(screen.getByRole("button", { name: /Update (it|them) to state\.priority/ }));
    const wf = useEditor.getState().wf!;
    expect(JSON.stringify(wf.nodes)).not.toMatch(/state\.urgency\b/);
    expect(wf.nodes.find((n) => n.id === "n_rt")!.config).toMatchObject({ cases: [{ when: 'state.priority == "high"' }] });
  });

  it("rewrites only whole references", () => {
    const wf = triage();
    wf.nodes[0] = { ...wf.nodes[0], config: { prompt: "{{ state.urgency }} {{ state.urgency_note }} state.urgency." } };
    const [cmd] = renameStateRefs(wf, "urgency", "priority");
    expect(cmd).toMatchObject({ op: "ReplaceNode", node: { config: { prompt: "{{ state.priority }} {{ state.urgency_note }} state.priority." } } });
  });
});
