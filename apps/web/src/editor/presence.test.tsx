// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WF } from "../ir/model";

const { apiFetch, getStream } = vi.hoisted(() => ({
  apiFetch: vi.fn(async (..._a: unknown[]): Promise<unknown> => ({ peers: [] })),
  getStream: vi.fn(async (..._a: unknown[]): Promise<void> => new Promise(() => undefined)),
}));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch, getStream }));
vi.mock("../run/sse", () => ({ getStream }));

const { colorOf, initials, others, savedNewer, selectingBlock, usePresence, usePresenceChannel } = await import("./presence");
const { NewerVersionNotice, PeerMarks, PresenceBar } = await import("./PresenceBar");
const { useEditor } = await import("./store");
type Peer = Parameters<typeof savedNewer>[0][number];

const peer = (session: string, user_id: string, name: string, selected: string | null = null, version: number | null = null): Peer =>
  ({ session, user_id, name, selected, version });

beforeEach(() => { usePresence.setState({ session: "mine", peers: [] }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); useEditor.setState({ version: 0, wf: null, workflowId: null }); });

describe("presence helpers", () => {
  it("shows initials and a colour that stays the same for a person", () => {
    expect([initials("Ada Lovelace"), initials("ada"), initials("  "), initials("grace-m-hopper")]).toEqual(["AL", "A", "?", "GH"]);
    expect(colorOf("usr_1")).toBe(colorOf("usr_1"));
    expect(colorOf("usr_1")).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("lists each other person once, with every block they have selected", () => {
    const peers = [
      peer("mine", "me", "Me", "n1"), peer("s2", "ada", "Ada", "n2", 4), peer("s3", "ada", "Ada", "n3"),
      peer("s4", "me", "Me"), peer("s5", "bob", "Bob"),
    ];
    expect(others(peers, "mine", "me")).toEqual([
      { user_id: "ada", name: "Ada", selected: ["n2", "n3"] },
      { user_id: "me", name: "You, in another tab", selected: [] },
      { user_id: "bob", name: "Bob", selected: [] },
    ]);
    expect(selectingBlock(peers, "mine", "n1")).toEqual([]); // not your own selection
    expect(selectingBlock(peers, "mine", "n2").map((p) => p.name)).toEqual(["Ada"]);
  });

  it("finds the people who saved a newer version than yours", () => {
    const peers = [peer("s2", "ada", "Ada", null, 7), peer("s3", "bob", "Bob", null, 5), peer("s4", "cy", "Cy", null, null), peer("mine", "me", "Me", null, 9)];
    expect(savedNewer(peers, "mine", 5).map((p) => p.name)).toEqual(["Ada"]);
    expect(savedNewer(peers, "mine", 8)).toEqual([]);
  });
});

describe("presence in the editor", () => {
  it("shows avatars with who selects what, and a mark on that block", () => {
    const wf = { nodes: [{ id: "n_cls", name: "classify", label: "Classify" }], resources: [] } as unknown as WF;
    useEditor.setState({ wf });
    usePresence.setState({ peers: [peer("mine", "me", "Me"), peer("s2", "ada", "Ada Lovelace", "n_cls", 3)] });
    render(<><PresenceBar /><PeerMarks id="n_cls" /><PeerMarks id="n_other" /></>);
    const avatar = screen.getByTitle("Ada Lovelace is selecting Classify");
    expect(avatar.textContent).toBe("AL");
    expect(screen.getAllByLabelText("selected by Ada Lovelace")).toHaveLength(1);
  });

  it("says when someone saved a newer version and reloads on request", () => {
    const load = vi.fn(async () => undefined);
    useEditor.setState({ version: 4, workflowId: "wf1", pending: [], inflight: false, load });
    usePresence.setState({ peers: [peer("s2", "ada", "Ada", null, 6)] });
    render(<NewerVersionNotice />);
    expect(screen.getByTestId("newer-version").textContent).toContain("Ada saved a newer version (v6; you have v4).");
    fireEvent.click(screen.getByRole("button", { name: "Update" }));
    expect(load).toHaveBeenCalledWith("wf1");
    cleanup();
    usePresence.setState({ peers: [peer("s2", "ada", "Ada", null, 4)] }); // the same version: nothing to say
    render(<NewerVersionNotice />);
    expect(screen.queryByTestId("newer-version")).toBeNull();
  });

  it("sends a heartbeat with the selection and version, listens to the stream and says goodbye", async () => {
    function Host({ id }: { id: string }) { usePresenceChannel(id); return null; }
    useEditor.setState({ selected: "n_cls", version: 3 });
    const { unmount } = render(<Host id="wf1" />);
    await vi.waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [path, init] = apiFetch.mock.calls[0] as [string, { method: string; json: { session: string; selected: string; version: number } }];
    expect(path).toBe("/workflows/wf1/presence");
    expect(init.method).toBe("POST");
    expect(init.json).toMatchObject({ selected: "n_cls", version: 3 });
    expect(init.json.session).toBe(usePresence.getState().session);
    expect(getStream.mock.calls[0][0]).toBe("/workflows/wf1/presence/stream");
    (getStream.mock.calls[0][1] as (m: { peers: Peer[] }) => void)({ peers: [peer("s2", "ada", "Ada")] }); // the server pushes
    expect(usePresence.getState().peers.map((p) => p.name)).toEqual(["Ada"]);
    const session = usePresence.getState().session;
    unmount();
    expect(apiFetch).toHaveBeenLastCalledWith(`/workflows/wf1/presence/${session}`, { method: "DELETE" });
    expect(usePresence.getState().peers).toEqual([]);
  });

  it("closes the stream while the tab is hidden and opens it again when it is shown", async () => {
    // over HTTP/1.1 a browser has 6 connections per site for all its tabs: a hidden editor must not hold one
    function Host({ id }: { id: string }) { usePresenceChannel(id); return null; }
    let hidden = false;
    const hiddenProp = Object.getOwnPropertyDescriptor(Document.prototype, "hidden");
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    try {
      const { unmount } = render(<Host id="wf1" />);
      await vi.waitFor(() => expect(getStream).toHaveBeenCalledTimes(1));
      const first = getStream.mock.calls[0][2] as AbortSignal;
      expect(first.aborted).toBe(false);
      const beats = apiFetch.mock.calls.length;

      hidden = true;
      document.dispatchEvent(new Event("visibilitychange"));
      expect(first.aborted).toBe(true);

      hidden = false;
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.waitFor(() => expect(getStream).toHaveBeenCalledTimes(2));
      expect((getStream.mock.calls[1][2] as AbortSignal).aborted).toBe(false);
      expect(apiFetch.mock.calls.length).toBeGreaterThan(beats); // back: tells the others at once, and hears who is here
      unmount();
      expect((getStream.mock.calls[1][2] as AbortSignal).aborted).toBe(true);
    } finally {
      delete (document as { hidden?: boolean }).hidden;
      if (hiddenProp) Object.defineProperty(Document.prototype, "hidden", hiddenProp);
    }
  });

  it("does not open the stream in a tab that starts hidden", async () => {
    function Host({ id }: { id: string }) { usePresenceChannel(id); return null; }
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    try {
      render(<Host id="wf1" />);
      await vi.waitFor(() => expect(apiFetch).toHaveBeenCalled()); // still says it is here
      expect(getStream).not.toHaveBeenCalled();
    } finally {
      delete (document as { hidden?: boolean }).hidden;
    }
  });
});
