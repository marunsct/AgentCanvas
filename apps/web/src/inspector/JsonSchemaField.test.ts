import { describe, expect, it } from "vitest";
import { rowsFromSchema, schemaFromRows } from "./JsonSchemaField";

describe("structured output designer (FR-AGENT-10)", () => {
  const schema = {
    title: "Triage",
    type: "object",
    properties: {
      urgency: { type: "string", enum: ["low", "high"], description: "how urgent" },
      tags: { type: "array", items: { type: "string" } },
      score: { type: "number" },
    },
    required: ["urgency"],
    additionalProperties: false,
  };

  it("round-trips a flat object schema through field rows", () => {
    const rows = rowsFromSchema(schema);
    expect(rows?.map((r) => [r.name, r.type, r.required])).toEqual([
      ["urgency", "enum", true], ["tags", "list", false], ["score", "number", false],
    ]);
    expect(schemaFromRows(rows!, "Triage")).toEqual(schema);
  });

  it("falls back to JSON for schemas the field list cannot show", () => {
    expect(rowsFromSchema({ type: "object", properties: { a: { type: "object", properties: {} } } })).toBeNull();
    expect(rowsFromSchema({ type: "object", oneOf: [] })).toBeNull();
    expect(rowsFromSchema(undefined)).toEqual([]);
  });

  it("no named fields means no schema (the option is cleared)", () => {
    expect(schemaFromRows([{ name: " ", type: "string", description: "", required: true, options: "" }])).toBeUndefined();
  });
});
