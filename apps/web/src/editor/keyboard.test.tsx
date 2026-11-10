// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

/** 20 widget types: "Add" matches more than the 12 the palette shows. */
const types = Array.from({ length: 20 }, (_, i) => ({ type: `t.${i}`, label: `Widget ${i}`, category: "c" }));
const addElement = vi.fn();
vi.mock("../api/queries", async (original) => ({ ...(await original<object>()), useNodeTypes: () => ({ data: types }) }));
vi.mock("../canvas/Canvas", () => ({ addElement: (...a: unknown[]) => addElement(...a) }));
vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, useCan: () => true, useCanOrg: () => true, useMe: () => "", ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { CommandPalette } = await import("./CommandPalette");
const { MoreMenu } = await import("./MoreMenu");
const { CopilotPanel } = await import("../copilot/CopilotPanel");
const { useUi } = await import("./ui");
const { useCopilot } = await import("../copilot/copilot");

afterEach(() => { cleanup(); addElement.mockClear(); });

describe("keyboard and IME (W-7)", () => {
  it("the command palette's cursor stops at the last match it shows", async () => {
    act(() => useUi.setState({ paletteOpen: true, paletteAt: null }));
    render(<MemoryRouter><CommandPalette onRun={() => undefined} onCopilot={() => undefined} /></MemoryRouter>);
    const search = screen.getByLabelText("Search commands");
    fireEvent.change(search, { target: { value: "Add" } });
    for (let i = 0; i < 15; i++) fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(search.getAttribute("aria-activedescendant")).toBe("palette-add:t.11");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(addElement).toHaveBeenCalledWith("t.11", undefined); // it ran Widget 15, a command nobody could see
  });

  it("the More menu takes the focus, moves with the arrow keys and gives the focus back on Escape", () => {
    render(<MemoryRouter><MoreMenu wfId="wf_1" blast={false} simple={false} /></MemoryRouter>);
    const trigger = screen.getByTestId("more-menu");
    trigger.focus();
    fireEvent.click(trigger);
    const items = [...screen.getByRole("menu").querySelectorAll<HTMLElement>("[role^=menuitem]")];
    expect(document.activeElement).toBe(items[0]);
    fireEvent.keyDown(items[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(items[1], { key: "ArrowUp" });
    fireEvent.keyDown(items[0], { key: "ArrowUp" });
    expect(document.activeElement).toBe(items.at(-1));
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("Enter that ends an IME composition in the Copilot does not send the message", () => {
    const send = vi.fn(async () => undefined);
    useCopilot.setState({
      send, load: vi.fn(async () => undefined), busy: false, live: null, error: null, request: null,
      conversation: { enabled: true, model: { model: "m" }, usage: {}, token_cap: 1000, messages: [], pending: null } as never,
    });
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><CopilotPanel /></MemoryRouter></QueryClientProvider>);
    const box = screen.getByLabelText("Message the Copilot");
    fireEvent.change(box, { target: { value: "にほんご" } });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true }); // picks the kanji, it used to send
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(send).toHaveBeenCalledWith("にほんご", "build");
  });

  it("a proposal shows the whole code of each file it writes before it is accepted (S-11)", () => {
    // tool output can steer what the Copilot writes, so the person must see every line they accept
    const content = Array.from({ length: 400 }, (_, i) => `line_${i} = ${i}`).join("\n") + "\nimport os; os.system('x')";
    useCopilot.setState({
      send: vi.fn(async () => undefined), load: vi.fn(async () => undefined), busy: false, live: null, error: null,
      request: null,
      conversation: {
        enabled: true, model: { model: "m" }, usage: {}, token_cap: 1000, messages: [],
        pending: { id: "p1", summary: "adds a tool", status: "pending", errors: 0, base_version: 1, commands: [],
          files: [{ path: "tools/x.py", content, before: null }] },
      } as never,
    });
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><CopilotPanel /></MemoryRouter></QueryClientProvider>);
    const shown = screen.getByTestId("copilot-proposal-files").querySelector("pre")?.textContent;
    expect(shown).toBe(content);
  });
});
