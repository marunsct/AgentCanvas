// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Command } from "../ir/commands";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { WorkflowPanel } = await import("./WorkflowPanel");

afterEach(cleanup);
const budget = (patch: Record<string, unknown>) => ({ op: "UpdateSettings", patch: { budget: patch } }) as Command;

describe("Budget fields (section 6)", () => {
  it("show the budget again when it changes elsewhere, and keep what is being typed", () => {
    useEditor.setState({ wf: triage(), workflowId: null, pending: [], notice: null });
    render(<QueryClientProvider client={new QueryClient()}><WorkflowPanel /></QueryClientProvider>);
    const tokens = screen.getByLabelText("budget Tokens per run") as HTMLInputElement;
    const alerts = screen.getByLabelText("budget alert at percent") as HTMLInputElement;
    fireEvent.change(tokens, { target: { value: "1000" } });
    expect(useEditor.getState().wf!.settings.budget).toMatchObject({ max_tokens: 1000 });
    act(() => useEditor.getState().dispatch([budget({ max_tokens: null, alert_at: [0.5, 0.9] })])); // as undo or redo would
    expect(tokens.value).toBe(""); // it kept showing 1000
    expect(alerts.value).toBe("50, 90");
    const usd = screen.getByLabelText("budget USD per run") as HTMLInputElement;
    fireEvent.change(usd, { target: { value: "0.0" } }); // on the way to 0.05: not a budget yet, and not thrown away
    expect(usd.value).toBe("0.0");
    fireEvent.change(usd, { target: { value: "0.05" } });
    expect(useEditor.getState().wf!.settings.budget).toMatchObject({ max_cost_usd: 0.05 });
  });
});
