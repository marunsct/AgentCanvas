import { describe, expect, it } from "vitest";
import { lineageAt } from "./lineage";
import type { Checkpoint } from "./Timeline";

const cp = (id: string, step: number, writes: Checkpoint["writes"]): Checkpoint =>
  ({ checkpoint_id: id, parent_checkpoint_id: null, step, source: "loop", next: [], writes, created_at: null });

describe("channel lineage (FR-X-18)", () => {
  const ticks = [
    cp("c0", -1, { __start__: { ticket: "hi" } }),
    cp("c1", 0, { n_cls: { urgency: "low" } }),
    cp("c2", 1, { n_rt: {} }),
    cp("c3", 2, { n_ag: { messages: [], urgency: "high" } }),
    cp("c4", 3, null),
  ];

  it("names the last writer and step of every channel at a checkpoint", () => {
    expect(lineageAt(ticks, 2)).toEqual({
      ticket: { writer: "input", step: 0, checkpointId: "c0" },
      urgency: { writer: "n_cls", step: 1, checkpointId: "c1" },
    });
    expect(lineageAt(ticks, 4).urgency).toEqual({ writer: "n_ag", step: 3, checkpointId: "c3" });
    expect(lineageAt(ticks, 0)).toEqual({});
  });
});
