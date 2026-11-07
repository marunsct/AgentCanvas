import { beforeEach, describe, expect, it, vi } from "vitest";
import { normalize } from "../ir/model";
import type { RunEvent } from "./events";

/** What the fake stream delivers before it ends. */
let events: RunEvent[] = [];
vi.mock("./sse", () => ({
  postStream: vi.fn(async (_path: string, _body: unknown, onEvent: (e: RunEvent) => void) => {
    for (const e of events) onEvent(e);
  }),
}));
vi.stubGlobal("requestAnimationFrame", (fn: () => void) => setTimeout(fn, 0) as unknown as number);
vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));

const { continueRun, forkRun, resumeRun, startRun } = await import("./session");
const { useRun } = await import("./runStore");
const { useEditor } = await import("../editor/store");
const { postStream } = await import("./sse");

describe("run stream (W-2)", () => {
  beforeEach(() => useRun.setState({ threadId: "t1", running: false, error: null }));

  it("ends the run when the stream closes without RUN_FINISHED or RUN_ERROR", async () => {
    events = [{ type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" }]; // then the connection drops
    await continueRun();
    expect(useRun.getState().running).toBe(false); // it used to stay "Running…" until a reload
    expect(useRun.getState().error).toMatch(/Connection lost/);
  });

  it("says nothing when the stream ended the run itself", async () => {
    events = [
      { type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" },
      { type: "RUN_FINISHED", threadId: "t1", outcome: "success" },
    ];
    await continueRun();
    expect(useRun.getState()).toMatchObject({ running: false, error: null });
  });
});

describe("startRun (W-4)", () => {
  it("starts one server run on a double-click of Run", async () => {
    useEditor.setState({ wf: normalize({ id: "wf_1", name: "w", state: { channels: [] } }), workflowId: null, breakpoints: [] });
    vi.mocked(postStream).mockClear();
    events = [{ type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" }, { type: "RUN_FINISHED", threadId: "t1", outcome: "success" }];
    await Promise.all([startRun({}), startRun({})]); // the second click lands before the first run's first event
    expect(postStream).toHaveBeenCalledTimes(1);
    expect(useRun.getState().starting).toBe(false);
    await startRun({}); // and Run works again once it has ended
    expect(postStream).toHaveBeenCalledTimes(2);
  });
});

describe("debug aids", () => {
  it("are not sent by a reviewer, who may approve but not debug", async () => {
    const { useMe } = await import("../api/session");
    const { useUi } = await import("../editor/ui");
    const me = useMe.getState().me;
    useEditor.setState({ wf: normalize({ id: "wf_1", name: "w", nodes: [{ id: "n1", type: "core.set", name: "a" }], state: { channels: [] } }),
      pins: { n1: { x: 1 } } });
    useUi.setState({ dryRun: true });
    useMe.setState({ me: { ...me, role: "reviewer" } });
    try {
      useRun.setState({ threadId: "t1", starting: false });
      vi.mocked(postStream).mockClear();
      events = [];
      await resumeRun({ approved: true }, "i1");
      expect(vi.mocked(postStream).mock.calls[0]![1]).toEqual({ value: { approved: true }, interrupt_id: "i1" });
    } finally {
      useMe.setState({ me });
      useUi.setState({ dryRun: false });
      useEditor.setState({ pins: {} });
    }
  });
});

describe("opening another thread during a run", () => {
  it("stops the old stream instead of mixing its events into the new thread", async () => {
    useRun.setState({ threadId: null, running: false, starting: false, error: null });
    vi.mocked(postStream).mockImplementationOnce(async (_p, _b, onEvent) => {
      onEvent({ type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" });
      await new Promise((r) => setTimeout(r, 5));
      useRun.getState().setThread("t2"); // a History row was clicked
      onEvent({ type: "STEP_STARTED", stepName: "a", canvas_node_id: "n1" });
      onEvent({ type: "RUN_FINISHED", threadId: "t1", outcome: "success", output: { answer: "for t1" } });
    });
    useEditor.setState({ wf: normalize({ id: "wf_1", name: "w", state: { channels: [] } }), breakpoints: [] });
    await startRun({});
    expect(useRun.getState()).toMatchObject({ threadId: "t2", running: false, error: null, finalOutput: null });
    expect(useRun.getState().status).toEqual({});
  });
});

describe("fork", () => {
  it("starts from a clean canvas, not the replaced run's colours, text and costs", async () => {
    useRun.setState({
      threadId: "t1", starting: false, status: { n1: "ok", n2: "error" }, output: { n1: "old answer" },
      metrics: { n1: { tokens: 10, cost: 0.1, priced: true, ms: 5 } },
    });
    events = [{ type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" }, { type: "STEP_STARTED", stepName: "b", canvas_node_id: "n2" }];
    await forkRun("cp1", {});
    expect(useRun.getState()).toMatchObject({ threadId: "t1", status: { n2: "running" }, output: {}, metrics: {} });
    expect([useRun.getState().status, useRun.getState().output, useRun.getState().metrics]).toEqual([{ n2: "running" }, {}, {}]);
  });
});

describe("approve, continue and fork", () => {
  it("send one request on a double-click", async () => {
    useRun.setState({ threadId: "t1", running: false, starting: false });
    events = [{ type: "RUN_STARTED", threadId: "t1", workflowId: "wf_1" }, { type: "RUN_FINISHED", threadId: "t1", outcome: "success" }];
    for (const act of [() => resumeRun({ approved: true }, "i1"), () => continueRun(), () => forkRun("cp1", {})]) {
      vi.mocked(postStream).mockClear();
      await Promise.all([act(), act()]); // the second click lands before the first stream's first event
      expect(postStream).toHaveBeenCalledTimes(1);
      expect(useRun.getState().starting).toBe(false);
    }
  });
});
