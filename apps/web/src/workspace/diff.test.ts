import { describe, expect, it } from "vitest";
import { lineDiff } from "./diff";
import { kindOf, starter } from "./starters";
import { withItemDefaults } from "../ui/rjsfTemplates";

describe("Copilot code diff (S17)", () => {
  it("marks added and removed lines and keeps the rest", () => {
    const d = lineDiff("a\nb\nc\n", "a\nB\nc\nd\n");
    expect(d.map((l) => `${l.kind}:${l.text}`)).toEqual(["same:a", "del:b", "add:B", "same:c", "add:d"]);
    expect(lineDiff("", "x\n")).toEqual([{ kind: "add", text: "x" }]);
    expect(lineDiff("same\n", "same\n")).toEqual([{ kind: "same", text: "same" }]);
  });
});

describe("starter code (S17)", () => {
  it("knows the folder of shared and per-workflow files", () => {
    expect(kindOf("scripts/a.py")).toBe("scripts");
    expect(kindOf("flows/wf_x/tools/a.py")).toBe("tools");
    expect(kindOf("flows/wf_x/helpers.py")).toBe("other");
    expect(starter("flows/wf_x/scripts/a.py")).toContain("def run(state: dict) -> dict:");
    expect(starter("tools/look up.py")).toContain("def look_up(query: str) -> str:");
    expect(starter("notes.md")).toBe("");
  });
});

describe("settings forms (S17)", () => {
  it("new list items start empty instead of null", () => {
    const schema = { type: "object", properties: { approve_tools: { type: "array", items: { type: "string" } },
      kinds: { type: "array", items: { type: "string", enum: ["a"] } } } };
    const out = withItemDefaults(schema) as typeof schema & { properties: { approve_tools: { items: { default?: string } }; kinds: { items: { default?: string } } } };
    expect(out.properties.approve_tools.items.default).toBe("");
    expect(out.properties.kinds.items.default).toBeUndefined(); // an enum keeps its own choice
    expect((schema.properties.approve_tools.items as { default?: string }).default).toBeUndefined(); // not mutated
  });
});
