import { afterEach, describe, expect, it, vi } from "vitest";
import { parseMessage, postStream } from "./sse";

describe("parseMessage", () => {
  it("reads every data line and skips a malformed one instead of ending the stream", () => {
    const msg = 'event: x\r\ndata: {"type":"RUN_STARTED"}\ndata: {not json\ndata:{"type":"RUN_FINISHED"}';
    expect(parseMessage(msg)).toEqual([{ type: "RUN_STARTED" }, { type: "RUN_FINISHED" }]);
  });
});

describe("postStream", () => {
  afterEach(() => vi.unstubAllGlobals());

  const reply = (...chunks: string[]) => new Response(new ReadableStream({
    start(c) { for (const x of chunks) c.enqueue(new TextEncoder().encode(x)); c.close(); },
  }), { status: 200 });

  it("delivers events split across chunks, and the last one when the connection closes without a blank line", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply('data: {"type":"RUN_STA', 'RTED"}\n\ndata: {"type":"RUN_', 'FINISHED"}')));
    const seen: string[] = [];
    await postStream("/x", {}, (e) => seen.push(e.type));
    expect(seen).toEqual(["RUN_STARTED", "RUN_FINISHED"]);
  });

  it("turns a non-2xx answer into an ApiError carrying the problem", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ title: "Not found", status: 404 }), { status: 404 })));
    await expect(postStream("/x", {}, () => undefined)).rejects.toMatchObject({ status: 404, problem: { title: "Not found" } });
  });

  it("survives an answer that is not JSON at all (a proxy's error page)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>502 Bad Gateway</html>", { status: 502 })));
    await expect(postStream("/x", {}, () => undefined)).rejects.toMatchObject({ status: 502 });
  });
});
