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

  it("treats our custom formats as always valid (Python regexes)", () => {
    const pii = { type: "object", properties: { detector: { type: "string", format: "regex" }, url: { type: "string", format: "uri" } } };
    for (const detector of ["(?P<ssn>\\d{3})", "(?i)secret"]) {
      expect(validator.validateFormData({ detector }, pii as never).errors).toEqual([]);
      expect(validator.isValid(pii as never, { detector }, pii as never)).toBe(true);
    }
    expect(validator.validateFormData({ url: "not a uri" }, pii as never).errors.map((e) => e.property)).toEqual([".url"]);
  });

  it("maps keys that are not plain names to bracket notation", () => {
    const s = { type: "object", required: ["a.b"], properties: { "x[0]": { type: "string" }, m: { type: "object", additionalProperties: { type: "integer" } } } };
    const { errors, errorSchema } = validator.validateFormData({ "x[0]": 1, m: { "k.1": "v", ok: "v" } }, s as never);
    expect(errors.map((e) => e.property).sort()).toEqual(['.m.ok', '.m["k.1"]', '["a.b"]', '["x[0]"]']);
    const es = errorSchema as Record<string, Record<string, { __errors?: string[] }> & { __errors?: string[] }>;
    expect(es["a.b"].__errors).toHaveLength(1);
    expect(es["x[0]"].__errors).toHaveLength(1);
    expect(es.m["k.1"].__errors).toHaveLength(1);
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
