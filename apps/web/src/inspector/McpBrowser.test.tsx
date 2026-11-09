// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { IRResource } from "../ir/model";

const dispatch = vi.fn();
vi.mock("../editor/store", () => ({ useEditor: (pick: (s: { dispatch: typeof dispatch }) => unknown) => pick({ dispatch }) }));
vi.mock("../api/client", () => ({ apiFetch: vi.fn() }));
vi.mock("../api/queries", () => ({ PROJECT_ID: "p1" }));

const { McpBrowser } = await import("./McpBrowser");

const server = (toolFilter?: string[]) => ({
  id: "r1", name: "files", type: "mcp.server",
  config: { discovered: [{ name: "read" }, { name: "write" }, { name: "delete" }], tool_filter: toolFilter },
}) as unknown as IRResource;
const filterSent = () => dispatch.mock.calls.at(-1)![0][0].patch.config.tool_filter;

afterEach(() => { cleanup(); dispatch.mockClear(); });

describe("McpBrowser tool filter (W-3)", () => {
  it("unticking one tool while all are offered offers the others, not only that one", () => {
    render(<McpBrowser resource={server()} />);
    fireEvent.click(screen.getByLabelText("offer delete"));
    expect(filterSent()).toEqual(["read", "write"]); // it used to send ["delete"]
  });

  it("ticking the last missing tool goes back to offering all of them", () => {
    render(<McpBrowser resource={server(["read", "write"])} />);
    fireEvent.click(screen.getByLabelText("offer delete"));
    expect(filterSent()).toBeNull();
  });

  it("keeps the last offered tool ticked: an empty filter would offer every tool", () => {
    render(<McpBrowser resource={server(["read"])} />);
    expect((screen.getByLabelText("offer read") as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("offer write"));
    expect(filterSent()).toEqual(["read", "write"]);
  });
});
