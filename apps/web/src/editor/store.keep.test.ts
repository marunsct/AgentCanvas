import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Command } from "../ir/commands";
import { normalize } from "../ir/model";

/** Requests the store made, and how the fake server answers the next commands request. */
const requests: { path: string; method: string; commands?: Command[]; version?: number }[] = [];
let conflictNext = false;
let getGate: Promise<void> | null = null; // while set, a reload (GET) waits for it

const wfOut = (version: number, name: string) => ({
  id: "wf_1", project_id: "prj_default", version, ir_hash: "h", diagnostics: [], inverse: [], layout: {},
  ir: { id: "wf_1", name, state: { channels: [] }, nodes: [], edges: [] },
});

vi.mock("../api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  }
  return {
    ApiError,
    apiFetch: vi.fn(async (path: string, opts: { method?: string; json?: { commands?: Command[] }; version?: number } = {}) => {
      requests.push({ path, method: opts.method ?? "GET", commands: opts.json?.commands, version: opts.version });
      if (opts.method === "POST" && conflictNext) {
        conflictNext = false;
        throw new ApiError(409, "version mismatch");
      }
      if (opts.method === "POST") return wfOut((opts.version ?? 0) + 1, "saved");
      if (getGate) await getGate;
      return wfOut(7, "changed elsewhere"); // GET: someone else saved version 7
    }),
  };
});
vi.mock("../api/session", () => ({ can: () => true, ME: { role: "builder" } }));

const { useEditor, hasUnsavedEdits } = await import("./store");
const rename = (name: string): Command => ({ op: "UpdateMeta", name }) as Command;
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("edits are kept", () => {
  beforeEach(() => {
    requests.length = 0;
    conflictNext = false;
    useEditor.setState({
      workflowId: "wf_1", version: 1, pending: [], inflight: false, notice: null,
      wf: normalize({ id: "wf_1", name: "a", state: { channels: [] }, nodes: [], edges: [] } as never),
    });
  });

  it("saves queued edits when another workflow is opened instead of dropping them", async () => {
    useEditor.getState().dispatch([rename("typed")], { debounceMs: 400 }); // still waiting for the debounce
    expect(hasUnsavedEdits()).toBe(true);
    useEditor.getState().reset();
    await tick();
    expect(requests).toEqual([{ path: "/workflows/wf_1/commands", method: "POST", commands: [rename("typed")], version: 1 }]);
    expect(useEditor.getState().workflowId).toBeNull();
  });

  it("re-applies the person's edits on the latest version after a conflict", async () => {
    conflictNext = true;
    useEditor.getState().dispatch([rename("mine")]);
    for (let i = 0; i < 6; i++) await tick();
    const posts = requests.filter((r) => r.method === "POST");
    expect(posts).toHaveLength(2); // refused with 409, then sent again on top of version 7
    expect(posts[1]).toMatchObject({ commands: [rename("mine")], version: 7 });
    expect(useEditor.getState().notice?.text).toMatch(/your edits were applied/);
    expect(useEditor.getState().pending).toEqual([]);
  });

  it("keeps edits typed while the latest version reloads after a conflict, and blocks undo meanwhile", async () => {
    conflictNext = true;
    let open!: () => void;
    getGate = new Promise((r) => { open = r; });
    try {
      useEditor.getState().dispatch([rename("mine")]);
      for (let i = 0; i < 4; i++) await tick();
      expect(requests.at(-1)?.method).toBe("GET"); // reloading
      useEditor.getState().dispatch([rename("during")]); // typed meanwhile
      expect(useEditor.getState().inflight).toBe(true); // so undo and redo wait
      open();
      for (let i = 0; i < 8; i++) await tick();
    } finally {
      getGate = null;
    }
    const posts = requests.filter((r) => r.method === "POST");
    expect(posts[1]).toMatchObject({ commands: [rename("mine"), rename("during")], version: 7 });
    expect(useEditor.getState()).toMatchObject({ pending: [], inflight: false });
    expect(useEditor.getState().wf?.name).toBe("saved");
  });
});
