// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EditorView } from "codemirror";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Fetch = (path: string, init?: { method?: string; json?: unknown }) => Promise<unknown>;
const apiFetch = vi.fn<Fetch>();
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: (p: string, i?: { method?: string; json?: unknown }) => apiFetch(p, i),
}));
vi.mock("./CodeHelp", () => ({ CodeHelp: () => null }));

const { WorkspacePage } = await import("./WorkspacePage");

// CodeMirror measures text ranges, which jsdom does not lay out
const empty = () => Object.assign([], { item: () => null }) as unknown as DOMRectList;
Range.prototype.getClientRects = empty;
Range.prototype.getBoundingClientRect = () => new DOMRect();

/** The CodeMirror view the page shows. */
const view = () => {
  const dom = screen.getByTestId("code-editor").querySelector(".cm-editor") as HTMLElement;
  return EditorView.findFromDOM(dom)!;
};
const type = (text: string) => act(() => { const v = view(); v.dispatch({ changes: { from: v.state.doc.length, insert: text } }); });
const text = () => view().state.doc.toString();

let server: Record<string, string>;
beforeEach(() => {
  server = { "tools/a.py": "x = 1\n" };
  apiFetch.mockReset();
  apiFetch.mockImplementation(async (path, init) => {
    if (path.endsWith("/files")) return Object.keys(server).map((p) => ({ path: p, size: 1, modified: 0 }));
    const file = decodeURIComponent(path.split("/files/")[1] ?? "");
    if (init?.method === "PUT") {
      server[file] = (init.json as { content: string }).content;
      return { path: file, size: 1, modified: 0, diagnostics: [] };
    }
    return { path: file, content: server[file], diagnostics: [] };
  });
});
afterEach(cleanup);

function renderPage(file = "tools/a.py") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/workspace?file=${file}`]}>
        <Routes><Route path="/workspace" element={<WorkspacePage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

const saveButton = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;

describe("WorkspacePage", () => {
  it("keeps what was typed while a save was on its way as unsaved", async () => {
    renderPage();
    await screen.findByTestId("code-editor");
    let answer: ((v: unknown) => void) | null = null;
    type("y = 2\n");
    apiFetch.mockImplementationOnce((_p, init) => {
      server["tools/a.py"] = (init!.json as { content: string }).content;
      return new Promise((r) => { answer = r; });
    });
    fireEvent.click(saveButton());
    await waitFor(() => expect(answer).not.toBeNull());
    type("# retry\n"); // typed while the server lints the saved file
    await act(async () => answer!({ path: "tools/a.py", size: 1, modified: 0, diagnostics: [] }));
    await waitFor(() => expect(saveButton().disabled).toBe(false));
    expect(screen.getByText("unsaved")).toBeTruthy();
    expect(text()).toBe("x = 1\ny = 2\n# retry\n");
    fireEvent.click(saveButton());
    await waitFor(() => expect(server["tools/a.py"]).toBe("x = 1\ny = 2\n# retry\n"));
    await waitFor(() => expect(screen.queryByText("unsaved")).toBeNull());
  });
});
