// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { manifests, triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { ConfigForm } = await import("./ConfigForm");

afterEach(cleanup);

describe("ConfigForm (W-11)", () => {
  it.each(["core.agent", "core.router", "mw.model_call_limit", "tool.http"])("selecting a %s block saves nothing", async (type) => {
    const dispatch = vi.fn();
    useEditor.setState({ wf: triage(), dispatch });
    const el = { id: "x", type, name: "x", config: {} } as never;
    render(<ConfigForm element={el} manifest={manifests.get(type)!} isResource={false} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(dispatch).not.toHaveBeenCalled(); // the form filling in its defaults used to be a save and an undo step
  });

  it("still saves what the person changes", async () => {
    const dispatch = vi.fn();
    useEditor.setState({ wf: triage(), dispatch });
    const el = { id: "x", type: "mw.model_call_limit", name: "x", config: {} } as never;
    const { container } = render(<ConfigForm element={el} manifest={manifests.get("mw.model_call_limit")!} isResource={false} />);
    const runLimit = container.querySelector<HTMLInputElement>("input[id$='run_limit']")!;
    await act(async () => { fireEvent.change(runLimit, { target: { value: "5" } }); });
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch.mock.calls[0][0][0]).toMatchObject({ op: "UpdateNode", id: "x", patch: { config: { run_limit: 5 } } });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
