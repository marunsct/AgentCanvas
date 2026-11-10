// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { triage } from "../test/fixtures";

/** What the fake server holds; DELETE requests are recorded and refused when `refuse` is set. */
const deletes: string[] = [];
let refuse = false;
vi.mock("../api/client", () => ({
  API_BASE: "/api/v1",
  ApiError: class extends Error {},
  apiFetch: vi.fn(async (path: string, init?: { method?: string }) => {
    if (init?.method === "DELETE") {
      deletes.push(path);
      if (refuse) throw new Error("the server said no");
      return {};
    }
    if (path.endsWith("/test-cases")) return [{ id: "tc1", name: "greets", last_result: null }];
    if (path.endsWith("/triggers")) return [{ id: "tr1", kind: "cron", enabled: true, config: { schedule: "0 9 * * *" } }];
    if (path.endsWith("/gates")) return [{ id: "g1", environment: "prod", dataset_id: "ds1", min_scores: {}, max_regression: null }];
    if (path.endsWith("/deployments")) return [{ id: "d1", environment: "prod", semver: "1.0.0", active: true }];
    if (path.endsWith("/online-evaluators")) return [{ id: "o1", evaluator: "helpful", sampling: 1, filter: {}, window_minutes: 60, min_samples: 5 }];
    return [];
  }),
}));
vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, useCan: () => true, useCanOrg: () => true, ME: { role: "admin" } }));

const { useEditor } = await import("../editor/store");
const { TestsPanel } = await import("./TestsPanel");
const { TriggersSection } = await import("./TriggersSection");
const { Gates } = await import("../evaluation/EvaluationPage");
const { Production } = await import("../evaluation/Production");
const { Inspector } = await import("../inspector/Inspector");

const confirm = vi.fn(() => false);
vi.stubGlobal("confirm", confirm);
const withQueries = (ui: ReactNode) =>
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>;

beforeEach(() => {
  deletes.length = 0;
  refuse = false;
  confirm.mockReset().mockReturnValue(false);
  useEditor.setState({ wf: triage(), workflowId: "wf_1", selected: null, notice: null, diagnostics: [] });
});
afterEach(cleanup);

/** Click `button`: nothing is deleted when the person says no; when yes, a refusal is shown. */
async function confirmsAndShowsFailure(button: () => HTMLElement) {
  await act(async () => { fireEvent.click(button()); });
  expect(confirm).toHaveBeenCalledTimes(1); // it used to delete at once
  expect(deletes).toEqual([]);
  confirm.mockReturnValue(true);
  refuse = true;
  await act(async () => { fireEvent.click(button()); });
  expect(deletes).toHaveLength(1);
  expect((await screen.findByRole("alert")).textContent).toContain("the server said no"); // it used to be dropped
}

describe("destructive actions ask first and show a failure (W-13)", () => {
  it("deleting a test case", async () => {
    render(withQueries(<TestsPanel />));
    await screen.findByText("greets");
    await confirmsAndShowsFailure(() => screen.getByLabelText("delete test greets"));
  });

  it("deleting a trigger", async () => {
    render(withQueries(<TriggersSection wfId="wf_1" />));
    await screen.findByText("0 9 * * *");
    await confirmsAndShowsFailure(() => screen.getByLabelText("delete trigger tr1"));
  });

  it("removing a release gate", async () => {
    render(withQueries(<Gates wfId="wf_1" />));
    await screen.findByTestId("gates");
    await confirmsAndShowsFailure(() => screen.getByRole("button", { name: "Remove" }));
  });

  it("removing an online evaluator", async () => {
    render(withQueries(<Production wfId="wf_1" />));
    await screen.findByTestId("online-evaluators");
    await confirmsAndShowsFailure(() => screen.getByRole("button", { name: "Remove" }));
  });

  it("deleting a node says it can be undone", async () => {
    useEditor.setState({ selected: "n_cls", dispatch: useEditor.getInitialState().dispatch });
    render(withQueries(<Inspector />));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Delete node" })); });
    expect(useEditor.getState().wf!.nodes.some((n) => n.id === "n_cls")).toBe(false);
    expect(useEditor.getState().notice).toEqual({ kind: "info", text: "Deleted classify. Undo with Ctrl/⌘+Z." });
  });
});
