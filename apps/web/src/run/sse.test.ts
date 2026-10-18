import { describe, expect, it } from "vitest";
import { parseMessage } from "./sse";

describe("parseMessage", () => {
  it("reads every data line and skips a malformed one instead of ending the stream", () => {
    const msg = 'event: x\r\ndata: {"type":"RUN_STARTED"}\ndata: {not json\ndata:{"type":"RUN_FINISHED"}';
    expect(parseMessage(msg)).toEqual([{ type: "RUN_STARTED" }, { type: "RUN_FINISHED" }]);
  });
});
