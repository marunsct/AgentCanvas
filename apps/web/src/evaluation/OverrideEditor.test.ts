import { describe, expect, it } from "vitest";
import { fromRows, toRows } from "./OverrideEditor";

describe("override rows", () => {
  it("turn into the overrides an experiment takes, and back", () => {
    const rows = [
      { element: "model", field: "model", value: "openai:gpt-5-mini" },
      { element: "model", field: "temperature", value: "0.2" },
      { element: "greet", field: "values", value: '{"out": "Hi"}' },
      { element: "", field: "x", value: "ignored" },
    ];
    const o = fromRows(rows);
    expect(o).toEqual({ model: { model: "openai:gpt-5-mini", temperature: 0.2 }, greet: { values: { out: "Hi" } } });
    expect(toRows(o)).toEqual(rows.slice(0, 2).concat([{ element: "greet", field: "values", value: '{"out":"Hi"}' }]));
  });
});
