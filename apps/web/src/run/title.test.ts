// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { useRun, waitingTitle } from "./runStore";

describe("tab title", () => {
  it("says (waiting) while a run in this tab waits for a decision", () => {
    document.title = "AgentCanvas";
    useRun.getState().apply({ type: "RUN_STARTED", threadId: "t1", workflowId: "w" });
    useRun.getState().apply({ type: "CUSTOM", name: "ac.interrupt", value: { id: "i1", kind: "approval", payload: {} } });
    expect(document.title).toBe("AgentCanvas"); // still running
    useRun.getState().apply({ type: "RUN_FINISHED", threadId: "t1", outcome: "interrupt" });
    expect(document.title).toBe("(waiting) AgentCanvas");
    useRun.getState().setThread(null); // decided, or left the workflow
    expect(document.title).toBe("AgentCanvas");
    expect(waitingTitle(waitingTitle("x", true), true)).toBe("(waiting) x");
  });
});
