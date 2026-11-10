// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { ProblemsPanel } = await import("./ProblemsPanel");

afterEach(cleanup);

describe("ProblemsPanel (W-6)", () => {
  it("shows an arrow's problem, focuses the arrow and offers to remove it", () => {
    const focusNode = vi.fn();
    const dispatch = vi.fn();
    useEditor.setState({
      wf: triage(), focusNode, dispatch,
      diagnostics: [{ code: "W012", severity: "warning", edge_id: "e3", message: "route 'high' is not a path of the router" }],
    });
    render(<ProblemsPanel />);
    const show = screen.getByRole("button", { name: /W012/ });
    expect((show as HTMLButtonElement).disabled).toBe(false); // it was a disabled button, a dead end
    expect(show.textContent).toContain("route_urgency → escalate");
    fireEvent.click(show);
    expect(focusNode).toHaveBeenCalledWith("e3");
    fireEvent.click(screen.getByRole("button", { name: "Remove this connection" }));
    expect(dispatch).toHaveBeenCalledWith([{ op: "Disconnect", id: "e3" }]);
  });
});
