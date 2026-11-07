// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

/** GET /workflows/{id} answers: each test resolves or rejects them in the order it wants. */
const loads = new Map<string, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn((path: string) => {
    const m = /^\/workflows\/(\w+)$/.exec(path);
    return new Promise((resolve, reject) => { if (m) loads.set(m[1], { resolve, reject }); }); // the rest never answers
  }),
}));

const { EditorPage } = await import("./EditorPage");
const { useEditor } = await import("./store");

const wfOut = (id: string) => ({
  id, project_id: "prj_default", version: 1, ir_hash: "h", diagnostics: [], layout: {}, ir: { id, name: id, state: { channels: [] }, nodes: [], edges: [] },
});

function openEditor(id: string) {
  const router = createMemoryRouter([{ path: "/workflows/:id", element: <EditorPage /> }], { initialEntries: [`/workflows/${id}`] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  return router;
}

afterEach(() => { cleanup(); loads.clear(); useEditor.getState().reset(); });

describe("EditorPage loading (W-5)", () => {
  it("clears a failed open when another workflow is opened", async () => {
    const router = openEditor("a");
    await act(async () => loads.get("a")!.reject(new Error("not found")));
    expect(screen.getByText(/Could not load workflow: not found/)).toBeTruthy();
    await act(async () => { await router.navigate("/workflows/b"); });
    expect(screen.queryByText(/Could not load workflow/)).toBeNull(); // it stayed until a reload
    expect(screen.getByRole("status").textContent).toMatch(/Loading workflow/);
  });

  it("ignores the first workflow's answer when it arrives after the second one's", async () => {
    const router = openEditor("a");
    await act(async () => { await router.navigate("/workflows/b"); });
    await act(async () => loads.get("b")!.resolve(wfOut("b")));
    await act(async () => loads.get("a")!.resolve(wfOut("a"))); // A was slower
    expect(useEditor.getState()).toMatchObject({ workflowId: "b", wf: { name: "b" } });
  });

  it("drops the last workflow's version comparison", async () => {
    const { useUi } = await import("./ui");
    useUi.getState().setDiff({ against: "1.0.0", added: ["n1"], changed: [] });
    openEditor("b");
    expect(useUi.getState().diff).toBeNull(); // its colours used to land on the next workflow's nodes
  });

  it("does not show the first workflow's load error on the second one", async () => {
    const router = openEditor("a");
    await act(async () => { await router.navigate("/workflows/b"); });
    await act(async () => loads.get("a")!.reject(new Error("not found")));
    expect(screen.queryByText(/Could not load workflow/)).toBeNull();
  });
});
