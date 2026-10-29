import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Command } from "../ir/commands";
import { normalize } from "../ir/model";

/** The commands requests the store made; the server refuses a rename to "taken" with 422 and stays at version 1. */
const posts: Command[][] = [];
let release: (() => void) | null = null;

const wfOut = (version: number, name: string) => ({
  id: "wf_1", project_id: "prj_default", version, ir_hash: "h", diagnostics: [], layout: {},
  ir: { id: "wf_1", name, state: { channels: [] }, nodes: [], edges: [] },
});

vi.mock("../api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  }
  return {
    ApiError,
    apiFetch: vi.fn(async (_path: string, opts: { method?: string; json?: { commands: Command[] }; version?: number } = {}) => {
      if (opts.method !== "POST") return wfOut(1, "a"); // GET: nothing changed on the server
      const commands = opts.json!.commands;
      posts.push(commands);
      await new Promise<void>((r) => (release = r)); // in flight until the test releases it
      if (commands.some((c) => c.op === "UpdateMeta" && c.name === "taken")) {
        throw new ApiError(422, "command #0 (UpdateMeta): the name is taken");
      }
      return { ...wfOut((opts.version ?? 0) + 1, "saved"), inverse: [{ op: "UpdateMeta", name: "a" }] };
    }),
  };
});
vi.mock("../api/session", () => ({ can: () => true, ME: { role: "builder" } }));

const { describeRefused, useEditor } = await import("./store");
const rename = (name: string): Command => ({ op: "UpdateMeta", name }) as Command;
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("a refused edit (W-1)", () => {
  beforeEach(() => {
    posts.length = 0;
    useEditor.setState({
      workflowId: "wf_1", version: 1, pending: [], inflight: false, notice: null,
      undoStack: [[rename("before")]], redoStack: [[rename("after")]],
      wf: normalize({ id: "wf_1", name: "a", state: { channels: [] }, nodes: [], edges: [] } as never),
    });
  });

  it("keeps undo/redo and the edits typed while it was in flight, and says which edit was refused", async () => {
    useEditor.getState().dispatch([rename("taken")]);
    await tick();
    expect(posts).toHaveLength(1);
    useEditor.getState().dispatch([{ op: "UpdateSettings", patch: { recursion_limit: 50 } } as Command]); // typed meanwhile
    release!();
    for (let i = 0; i < 4; i++) await tick();
    const s = useEditor.getState();
    expect(s.undoStack).toEqual([[rename("before")]]); // the reload used to wipe both stacks
    expect(s.redoStack).toEqual([[rename("after")]]);
    expect(s.notice).toEqual({ kind: "error", text: "Edit rejected: UpdateMeta: the name is taken." });
    expect(posts).toHaveLength(2); // the later edit is sent again on its own
    expect(posts[1]).toEqual([{ op: "UpdateSettings", patch: { recursion_limit: 50 } }]);
    expect(s.wf?.name).toBe("a");
    expect(s.wf?.settings).toMatchObject({ recursion_limit: 50 });
    release!();
    for (let i = 0; i < 4; i++) await tick();
    expect(useEditor.getState()).toMatchObject({ version: 2, pending: [], inflight: false });
  });

  it("names the refused command by its target", () => {
    const batch = [rename("x"), { op: "RemoveNode", id: "n_7" } as Command];
    expect(describeRefused(batch, "command #1 (RemoveNode): unknown node 'n_7'")).toBe("RemoveNode 'n_7': unknown node 'n_7'.");
    expect(describeRefused(batch, "the result is not a valid workflow: bad")).toBe("the result is not a valid workflow: bad.");
  });
});
