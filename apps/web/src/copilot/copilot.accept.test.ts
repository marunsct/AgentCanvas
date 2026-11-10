import { describe, expect, it, vi } from "vitest";
import type { Command } from "../ir/commands";
import { normalize } from "../ir/model";

const requests: { path: string; method: string; json?: unknown }[] = [];
let conflictNext = true;
const wfOut = (version: number) => ({
  id: "wf_1", project_id: "prj_default", version, ir_hash: "h", diagnostics: [], inverse: [], layout: {},
  ir: { id: "wf_1", name: `v${version}`, state: { channels: [] }, nodes: [], edges: [] },
});

vi.mock("../api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, message: string) { super(message); }
  }
  return {
    ApiError,
    apiFetch: vi.fn(async (path: string, opts: { method?: string; json?: unknown; version?: number } = {}) => {
      const method = opts.method ?? "GET";
      requests.push({ path, method, json: opts.json });
      if (path.endsWith("/commands")) {
        if (conflictNext) { conflictNext = false; throw new ApiError(409, "version mismatch"); } // someone saved meanwhile
        return wfOut((opts.version ?? 0) + 1);
      }
      if (path === "/workflows/wf_1") return wfOut(7);
      if (path.endsWith("/copilot")) return { id: "c", enabled: true, messages: [], proposals: [], pending: null, usage: {}, model: {} };
      return {};
    }),
  };
});
vi.mock("../api/session", () => ({ can: () => true, canOrg: () => true, ME: { role: "builder" } }));

const { useEditor } = await import("../editor/store");
const { useCopilot } = await import("./copilot");

describe("accepting a Copilot proposal (W-11)", () => {
  it("is recorded when the edits land on the latest version after a conflict", async () => {
    useEditor.setState({
      workflowId: "wf_1", version: 1, pending: [], inflight: false, notice: null,
      wf: normalize({ id: "wf_1", name: "a", state: { channels: [] }, nodes: [], edges: [] } as never),
    });
    const commands = [{ op: "UpdateMeta", name: "proposed" } as Command];
    useCopilot.setState({
      workflowId: "wf_1", selection: null, conversation: { pending: { id: "p1", status: "pending", stale: false, commands } } as never,
    });
    await useCopilot.getState().accept();
    expect(useEditor.getState().notice).toMatchObject({ kind: "info" }); // "your edits were applied to the latest version"
    expect(useEditor.getState().version).toBe(8);
    // it used to take the info notice for a failure: the edits landed, the proposal stayed open, and Accept again failed
    expect(requests).toContainEqual(expect.objectContaining({
      path: "/workflows/wf_1/copilot/proposals/p1", method: "POST", json: expect.objectContaining({ status: "accepted" }),
    }));
  });
});
