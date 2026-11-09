// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { manifests, triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { ConfigForm } = await import("./ConfigForm");

afterEach(cleanup);

describe("ConfigForm of a router (W-6)", () => {
  it("removes the arrow of a path whose rule is removed, in the same edit", async () => {
    const wf = triage();
    const dispatch = vi.fn();
    useEditor.setState({ wf, dispatch });
    const router = wf.nodes.find((n) => n.id === "n_rt")!;
    render(<ConfigForm element={router} manifest={manifests.get("core.router")!} isResource={false} />);
    dispatch.mockClear(); // whatever the form sends when it mounts
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Remove this item" })); });
    const cmds = dispatch.mock.calls.at(-1)![0];
    expect(cmds).toContainEqual({ op: "Disconnect", id: "e3" }); // it stayed, invisible, with no "high" dot to leave from
    expect(cmds).not.toContainEqual({ op: "Disconnect", id: "e4" });
  });
});
