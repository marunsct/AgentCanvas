// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RunEvent } from "./events";
import { BATCH_LIMIT, batcher } from "./session";

const token: RunEvent = { type: "TEXT_MESSAGE_CONTENT", canvas_node_id: "n1", delta: "x" };

describe("event batcher", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1)); // a frame that never comes, as in a hidden tab
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("still applies events in a hidden tab, which gets no animation frames", () => {
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const seen: RunEvent[] = [];
    const b = batcher((e) => seen.push(e));
    b.push(token);
    b.push(token);
    vi.advanceTimersByTime(200);
    expect(seen).toHaveLength(2);
  });

  it("applies a long queue at once rather than holding it for a frame", () => {
    const seen: RunEvent[] = [];
    const b = batcher((e) => seen.push(e));
    for (let i = 0; i < BATCH_LIMIT; i++) b.push(token);
    expect(seen).toHaveLength(BATCH_LIMIT);
  });
});
