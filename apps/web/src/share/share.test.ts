import { describe, expect, it, vi } from "vitest";
import { embedSnippet, friendly, isEmbed, parseSse, sendMessage, tokenFromPath, type ShareEvent } from "./share";

describe("share links (S23)", () => {
  it("reads the token and embed mode from the page address", () => {
    expect(tokenFromPath("/s/sh_abc-DEF_123")).toBe("sh_abc-DEF_123");
    expect(tokenFromPath("/s/sh_abc/")).toBe("sh_abc");
    expect(tokenFromPath("/s/../x")).toBeNull();
    expect(tokenFromPath("/workflows/wf_1")).toBeNull();
    expect(isEmbed("?embed=1")).toBe(true);
    expect(isEmbed("")).toBe(false);
  });

  it("parses the run stream across chunk boundaries", () => {
    const seen: ShareEvent[] = [];
    let rest = parseSse('data: {"type":"RUN_STARTED","runId":"r","threadId":"t"}\n\ndata: {"type":"TEXT_', (e) => seen.push(e));
    rest = parseSse(rest + 'MESSAGE_CONTENT","delta":"Hi"}\n\n', (e) => seen.push(e));
    expect(rest).toBe("");
    expect(seen.map((e) => e.type)).toEqual(["RUN_STARTED", "TEXT_MESSAGE_CONTENT"]);
  });

  it("skips a malformed frame instead of ending the reply (W-12)", () => {
    const seen: ShareEvent[] = [];
    const rest = parseSse('data: {"type":"TEXT_MESSAGE_CONTENT","delta":"A"}\n\ndata: {not json\n\n' +
      'data:{"type":"TEXT_MESSAGE_CONTENT","delta":"B"}\n\n', (e) => seen.push(e));
    expect(rest).toBe("");
    expect(seen.map((e) => (e as { delta?: string }).delta)).toEqual(["A", "B"]); // the bad frame used to throw
  });

  it("reads messages framed with \\r\\n, and a last message without a blank line after it", async () => {
    const seen: ShareEvent[] = [];
    expect(parseSse('data: {"type":"RUN_STARTED","runId":"r","threadId":"t"}\r\n\r\n', (e) => seen.push(e))).toBe("");
    expect(seen.map((e) => e.type)).toEqual(["RUN_STARTED"]);
    const body = 'data: {"type":"TEXT_MESSAGE_CONTENT","delta":"Hi"}\n\ndata: {"type":"RUN_FINISHED","threadId":"t"}\n';
    const fetchMock = vi.fn(async () => new Response(body, { headers: { "content-type": "text/event-stream" } }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const got: ShareEvent[] = [];
      await sendMessage("sh_x", "hello", null, (e) => got.push(e));
      expect(got.map((e) => e.type)).toEqual(["TEXT_MESSAGE_CONTENT", "RUN_FINISHED"]); // the end used to be dropped
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("explains failures in plain words and makes the embed snippet", () => {
    expect(friendly(404)).toMatch(/does not work any more/);
    expect(friendly(429)).toMatch(/wait a minute/);
    expect(embedSnippet("https://ac.example.com", "sh_x")).toBe(
      '<script src="https://ac.example.com/widget.js" data-share="sh_x" async></script>',
    );
  });
});
