import { describe, expect, it } from "vitest";
import { formSchema } from "./EvaluatorForm";

describe("formSchema", () => {
  it("turns pydantic's optional fields (anyOf X or null) into plain fields a form can show", () => {
    const s = formSchema({
      type: "object",
      properties: {
        channels: { anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }], default: null, title: "Channels" },
        mode: { enum: ["a", "b"], type: "string", default: "a" },
        either: { anyOf: [{ type: "string" }, { type: "integer" }] },
      },
    });
    const p = s.properties as Record<string, Record<string, unknown>>;
    expect(p.channels).toEqual({ type: "array", items: { type: "string", default: "" }, title: "Channels" });
    expect(p.mode.default).toBe("a");
    expect(Array.isArray(p.either.anyOf)).toBe(true); // a real choice stays a choice
  });
});
