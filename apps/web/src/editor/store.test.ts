import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Command } from "../ir/commands";
import { normalize } from "../ir/model";

const calls: { commands: Command[]; release: (inverse?: Command[]) => void }[] = [];
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  apiFetch: vi.fn((_path: string, opts: { json: { commands: Command[] }; version: number }) => {
    // each commands request stays in flight until the test releases it
    return new Promise((resolve) => {
      calls.push({
        commands: opts.json.commands,
        release: (inverse: Command[] = []) => resolve({
          version: opts.version + 1, ir_hash: "h", diagnostics: [], inverse,
          ir: { id: "wf_1", name: String(opts.version + 1), state: { channels: [] }, nodes: [], edges: [] },
        }),
      });
    });
  }),
}));

const { useEditor } = await import("./store");
const rename = (name: string): Command => ({ op: "UpdateMeta", name }) as Command;
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("flush", () => {
  beforeEach(() => {
    calls.length = 0;
    useEditor.setState({
      workflowId: "wf_1", version: 1, pending: [], inflight: false,
      wf: normalize({ id: "wf_1", name: "a", state: { channels: [] }, nodes: [], edges: [] } as never),
    });
  });

  it("waits for the save in flight, then saves what was queued meanwhile", async () => {
    useEditor.setState({ pending: [rename("b")] });
    const first = useEditor.getState().flush();
    await tick();
    expect(calls).toHaveLength(1);
    useEditor.setState({ pending: [rename("c")] }); // an edit made while the first save is in flight
    let done = false;
    const second = useEditor.getState().flush().then(() => (done = true));
    await tick();
    expect(done).toBe(false); // it used to return at once, so a run started on the unsaved workflow
    calls[0].release();
    await tick();
    expect(calls).toHaveLength(2);
    expect(calls[1].commands).toEqual([rename("c")]);
    expect(done).toBe(false);
    calls[1].release();
    await Promise.all([first, second]);
    expect(done).toBe(true);
    expect(useEditor.getState()).toMatchObject({ version: 3, pending: [], inflight: false });
  });

  it("undoes once the save in flight has landed, instead of ignoring the press", async () => {
    useEditor.setState({ undoStack: [], redoStack: [] });
    useEditor.getState().dispatch([rename("b")]);
    await tick();
    expect(calls).toHaveLength(1); // saving "b"
    useEditor.getState().undo(); // Ctrl+Z while it is saved
    await tick();
    expect(calls).toHaveLength(1);
    calls[0].release([rename("a")]); // the inverse of "b"
    for (let i = 0; i < 4; i++) await tick();
    expect(calls).toHaveLength(2);
    expect(calls[1].commands).toEqual([rename("a")]);
    expect(useEditor.getState().wf?.name).toBe("a");
    calls[1].release([rename("b")]);
    for (let i = 0; i < 4; i++) await tick();
    expect(useEditor.getState()).toMatchObject({ undoStack: [], redoStack: [[rename("b")]], pending: [], inflight: false });
  });
});
