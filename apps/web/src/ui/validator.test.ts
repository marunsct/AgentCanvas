import { describe, expect, it } from "vitest";
import { validator } from "./validator";

const schema = {
  type: "object", required: ["name"], additionalProperties: false,
  properties: { name: { type: "string" }, n: { type: "integer", format: "cel" }, tags: { type: "array", items: { type: "string" } } },
} as const;

describe("interpreting validator", () => {
  it("reports leaf errors the way RJSF expects", () => {
    const { errors, errorSchema } = validator.validateFormData({ n: "x", tags: [1], extra: true }, schema as never);
    expect(errors.map((e) => e.property).sort()).toEqual([".extra", ".n", ".name", ".tags.0"]);
    expect(errors.find((e) => e.property === ".name")?.message).toBe("must have required property 'name'");
    expect((errorSchema as Record<string, { __errors?: string[] }>).extra.__errors).toEqual(["must NOT have additional properties"]);
  });

  it("accepts valid data, ignores custom formats and resolves the root's definitions", () => {
    expect(validator.validateFormData({ name: "a", n: 3 }, schema as never).errors).toEqual([]);
    const root = { definitions: { X: { type: "integer" as const } } };
    expect(validator.isValid({ $ref: "#/definitions/X" }, 3, root)).toBe(true);
    expect(validator.isValid({ $ref: "#/definitions/X" }, "3", root)).toBe(false);
  });

  it("never compiles code", () => {
    const original = globalThis.Function;
    globalThis.Function = (() => { throw new Error("eval is blocked"); }) as never;
    try {
      expect(validator.validateFormData({ name: 1 }, { type: "object", properties: { name: { type: "string" } } }).errors).toHaveLength(1);
    } finally {
      globalThis.Function = original;
    }
  });
});
