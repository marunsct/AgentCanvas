// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NodeManifest } from "../api/types";
// the API's own manifest: the generated catalog copy is refreshed separately
import waitManifest from "../../../api/agentcanvas/registry/nodes/core.wait.json";
import { triage } from "../test/fixtures";

vi.mock("../api/session", () => ({ can: () => true, ME: { role: "builder" } }));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch: vi.fn(() => new Promise(() => undefined)) }));

const { useEditor } = await import("../editor/store");
const { ConfigForm } = await import("./ConfigForm");
const manifest = waitManifest as unknown as NodeManifest;

afterEach(cleanup);

function shown(container: HTMLElement): string[] {
  return ["duration", "until", "timezone", "event", "event_key", "timeout"].filter((k) => {
    const el = container.querySelector<HTMLInputElement>(`[id$='_${k}']`);
    return el !== null && el.type !== "hidden";
  });
}

describe("Wait block in the Inspector", () => {
  it("shows the settings of its way of waiting and says what a duration means", async () => {
    const dispatch = vi.fn();
    useEditor.setState({ wf: triage(), dispatch });
    const el = { id: "w", type: "core.wait", name: "wait", config: { duration: "2h 30m" } } as never;
    const { container, rerender } = render(<ConfigForm element={el} manifest={manifest} isResource={false} />);
    expect(shown(container)).toEqual(["duration"]);
    expect(screen.getByText("waits 2 hours 30 minutes")).toBeTruthy();
    expect(screen.getByRole("option", { name: "Until an event arrives" })).toBeTruthy();
    const duration = container.querySelector<HTMLInputElement>("[id$='_duration']")!;
    await act(async () => { fireEvent.change(duration, { target: { value: "soon" } }); });
    expect(dispatch.mock.calls.at(-1)![0][0]).toMatchObject({ op: "UpdateNode", patch: { config: { duration: "soon" } } });
    const event = { id: "w", type: "core.wait", name: "wait", config: { mode: "event", event: "invoice.paid" } } as never;
    rerender(<ConfigForm element={event} manifest={manifest} isResource={false} />);
    expect(shown(container)).toEqual(["event", "event_key", "timeout"]);
  });
});
