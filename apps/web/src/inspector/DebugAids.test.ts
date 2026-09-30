import { describe, expect, it } from "vitest";
import { parseReplies } from "./DebugAids";

describe("mock replies (M4-T9)", () => {
  it("one reply per line; JSON lines become tool-call replies; bad JSON stays text", () => {
    expect(parseReplies('low\n\n {"tool_calls": [{"name": "send_reply", "args": {"text": "Hi"}}]}\n{oops')).toEqual([
      "low",
      { tool_calls: [{ name: "send_reply", args: { text: "Hi" } }] },
      "{oops",
    ]);
  });
});
