// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EditorView } from "codemirror";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Init = { method?: string; json?: unknown; headers?: Record<string, string> };
type Fetch = (path: string, init?: Init) => Promise<unknown>;
const apiFetch = vi.fn<Fetch>();
class ApiError extends Error {
  constructor(public status: number, public problem: Record<string, unknown>) { super(String(problem.detail)); }
}
vi.mock("../api/client", () => ({ ApiError, apiFetch: (p: string, i?: Init) => apiFetch(p, i) }));
vi.mock("./CodeHelp", () => ({ CodeHelp: () => null }));

const { WorkspacePage } = await import("./WorkspacePage");
const { DialogHost } = await import("../ui/dialog");

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
const ver = (content: string) => `h:${content}`; // the server's version is a hash of the text
beforeEach(() => {
  server = { "tools/a.py": "x = 1\n" };
  apiFetch.mockReset();
  apiFetch.mockImplementation(async (path, init) => {
    if (path.endsWith("/files")) return Object.keys(server).map((p) => ({ path: p, size: 1, modified: 0 }));
    const file = decodeURIComponent(path.split("/files/")[1] ?? "");
    if (init?.method === "PUT") {
      const base = init.headers?.["If-Match"]?.replaceAll('"', "");
      if (base !== undefined && base !== ver(server[file])) {
        throw new ApiError(409, { detail: `'${file}' was changed by someone else since you opened it`, version: ver(server[file]) });
      }
      server[file] = (init.json as { content: string }).content;
      return { path: file, size: 1, modified: 0, diagnostics: [], version: ver(server[file]) };
    }
    return { path: file, content: server[file], diagnostics: [], version: ver(server[file]) };
  });
});
afterEach(cleanup);

let router: ReturnType<typeof createMemoryRouter>;
function renderPage(file = "tools/a.py") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  router = createMemoryRouter([
    { path: "/", element: <p>Home</p> },
    { path: "/workspace", element: <WorkspacePage /> },
    { path: "/settings/:section", element: <p>Settings page</p> },
  ], { initialEntries: ["/", `/workspace?file=${file}`], initialIndex: 1 });
  render(
    <QueryClientProvider client={qc}>
      <DialogHost />
      <RouterProvider router={router} />
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
    await act(async () => answer!({ path: "tools/a.py", size: 1, modified: 0, diagnostics: [], version: ver(server["tools/a.py"]) }));
    await waitFor(() => expect(saveButton().disabled).toBe(false));
    expect(screen.getByText("unsaved")).toBeTruthy();
    expect(text()).toBe("x = 1\ny = 2\n# retry\n");
    fireEvent.click(saveButton());
    await waitFor(() => expect(server["tools/a.py"]).toBe("x = 1\ny = 2\n# retry\n"));
    await waitFor(() => expect(screen.queryByText("unsaved")).toBeNull());
  });

  it("shows a newer copy of the file when nothing was typed", async () => {
    const qc = renderPage();
    await screen.findByTestId("code-editor");
    server["tools/a.py"] = "x = 2\n"; // the Copilot, a teammate or a Git pull changed it
    await act(() => qc.invalidateQueries({ queryKey: ["file"] }));
    await waitFor(() => expect(text()).toBe("x = 2\n"));
    expect(screen.queryByText("unsaved")).toBeNull();
  });

  it("does not save over a newer copy: it says so, and saves only when told to keep this one", async () => {
    renderPage();
    await screen.findByTestId("code-editor");
    type("y = 1\n");
    server["tools/a.py"] = "x = 2\n"; // changed elsewhere while this was being edited
    fireEvent.click(saveButton());
    expect(await screen.findByText(/changed by someone else/)).toBeTruthy();
    expect(server["tools/a.py"]).toBe("x = 2\n");
    const notice = await screen.findByTestId("file-changed");
    expect(text()).toBe("x = 1\ny = 1\n"); // what was typed stays
    fireEvent.click(within(notice).getByRole("button", { name: /Keep mine/ }));
    await waitFor(() => expect(saveButton().disabled).toBe(false));
    fireEvent.click(saveButton());
    await waitFor(() => expect(server["tools/a.py"]).toBe("x = 1\ny = 1\n"));
    await waitFor(() => expect(screen.queryByTestId("file-changed")).toBeNull());
  });

  it("loads the newer copy when asked, in place of the unsaved text", async () => {
    const qc = renderPage();
    await screen.findByTestId("code-editor");
    type("y = 1\n");
    server["tools/a.py"] = "x = 2\n";
    await act(() => qc.invalidateQueries({ queryKey: ["file"] }));
    const notice = await screen.findByTestId("file-changed");
    expect(text()).toBe("x = 1\ny = 1\n");
    fireEvent.click(within(notice).getByRole("button", { name: /Load the new version/ }));
    await waitFor(() => expect(text()).toBe("x = 2\n"));
    expect(screen.queryByText("unsaved")).toBeNull();
    expect(screen.queryByTestId("file-changed")).toBeNull();
  });

  it("asks before another page replaces unsaved changes, and stays when told to keep editing", async () => {
    renderPage();
    await screen.findByTestId("code-editor");
    type("y = 1\n");
    fireEvent.click(screen.getByTestId("secrets-link"));
    const ask = await screen.findByRole("dialog", { name: /Discard your changes/ });
    fireEvent.click(within(ask).getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(router.state.location.pathname).toBe("/workspace");
    expect(text()).toBe("x = 1\ny = 1\n");
    expect(screen.getByText("unsaved")).toBeTruthy();
    // browser Back is asked about too; discarding leaves
    await act(() => router.navigate(-1));
    fireEvent.click(within(await screen.findByRole("dialog", { name: /Discard your changes/ })).getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByText("Home")).toBeTruthy();
  });

  it("leaves without asking when nothing is unsaved", async () => {
    renderPage();
    await screen.findByTestId("code-editor");
    fireEvent.click(screen.getByTestId("secrets-link"));
    expect(await screen.findByText("Settings page")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
