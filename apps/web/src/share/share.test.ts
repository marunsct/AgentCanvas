import { describe, expect, it } from "vitest";
import { embedSnippet, friendly, isEmbed, parseSse, tokenFromPath, type ShareEvent } from "./share";

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

  it("explains failures in plain words and makes the embed snippet", () => {
    expect(friendly(404)).toMatch(/does not work any more/);
    expect(friendly(429)).toMatch(/wait a minute/);
    expect(embedSnippet("https://ac.example.com", "sh_x")).toBe(
      '<script src="https://ac.example.com/widget.js" data-share="sh_x" async></script>',
    );
  });
});
