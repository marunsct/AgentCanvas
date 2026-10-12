import { describe, expect, it } from "vitest";
import { checkFiles, chunkingLabel, citation, embeddingLabel, NAME_PATTERN, suggestName } from "./knowledge";

describe("knowledge helpers (S24)", () => {
  it("suggests names the server accepts", () => {
    expect(suggestName("HR Handbook 2026!")).toBe("hr-handbook-2026");
    expect(NAME_PATTERN.test(suggestName("  Product docs "))).toBe(true);
    expect(NAME_PATTERN.test("Bad Name")).toBe(false);
  });

  it("drops a trailing dash, so the page keeps what was typed and names it only when sending", () => {
    expect(suggestName("hr ")).toBe("hr"); // applied per keystroke, "hr handbook" could never be typed
    expect(suggestName("hr handbook")).toBe("hr-handbook");
  });

  it("refuses unknown types and big files before uploading", () => {
    const { ok, refused } = checkFiles([
      { name: "guide.PDF", size: 10 },
      { name: "tool.exe", size: 10 },
      { name: "huge.txt", size: 30 * 1024 * 1024 },
      { name: "README", size: 10 },
    ]);
    expect(ok).toEqual([0]);
    expect(refused).toEqual(["tool.exe: not a supported file type", "huge.txt: larger than 20 MB", "README: not a supported file type"]);
  });

  it("reads citations and settings", () => {
    expect(citation({ cite: 2, title: "Travel", page: 3, heading: "Flights" })).toBe("[2] Travel, Flights, page 3");
    expect(citation({ cite: 1, title: null, page: null, heading: null })).toBe("[1] untitled");
    expect(chunkingLabel({ chunking: { strategy: "fixed", size: 400, overlap: 50 } })).toBe("Fixed size, about 400 characters (overlap 50)");
    expect(chunkingLabel({ chunking: { strategy: "markdown", size: 800, overlap: 100 } })).toBe("By heading, about 800 characters");
    expect(embeddingLabel({ embedding: { model: "builtin" } })).toBe("Built-in (offline)");
    expect(embeddingLabel({ embedding: { model: "openai:text-embedding-3-small" } })).toBe("openai:text-embedding-3-small");
  });
});
