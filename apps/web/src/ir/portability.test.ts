import { describe, expect, it } from "vitest";
import type { AdapterManifest } from "../api/types";
import { portability, support } from "./portability";

const lc = { id: "langchain", label: "LangGraph", nodes: { "*": "native" } } as unknown as AdapterManifest;
const echo = {
  id: "echo", label: "Echo", nodes: { "core.set": "native", "core.llm": "emulated", "mw.*": "native", "*": "unsupported" },
} as unknown as AdapterManifest;

describe("portability", () => {
  it("looks up exact types, then namespace and catch-all fallbacks", () => {
    expect(support(echo, "core.set")).toBe("native");
    expect(support(echo, "mw.pii")).toBe("native");
    expect(support(echo, "core.agent")).toBe("unsupported");
    expect(support(lc, "anything.at_all")).toBe("native");
  });

  it("names the blocks that stop or bend each framework", () => {
    const wf = {
      nodes: [
        { id: "n1", type: "core.set", name: "a", config: {} },
        { id: "n2", type: "core.llm", name: "b", config: {} },
        { id: "n3", type: "core.agent", name: "c", config: {} },
      ],
      resources: [],
    };
    const [a, b] = portability(wf as never, [lc, echo]);
    expect(a.ok).toBe(true);
    expect(b.ok).toBe(false);
    expect(b.blockers.map((x) => x.name)).toEqual(["c"]);
    expect(b.emulated.map((x) => x.name)).toEqual(["b"]);
  });
});
