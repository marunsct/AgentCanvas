import { describe, expect, it } from "vitest";
import { readValue } from "./ExampleForm";

describe("readValue", () => {
  it("reads numbers, booleans, lists and objects as JSON and everything else as text", () => {
    expect(readValue("42")).toBe(42);
    expect(readValue("true")).toBe(true);
    expect(readValue('["a", 1]')).toEqual(["a", 1]);
    expect(readValue('{"a": 1}')).toEqual({ a: 1 });
    expect(readValue("Hello Ada")).toBe("Hello Ada");
    expect(readValue("{not json}")).toBe("{not json}");
    expect(readValue("007 is a spy")).toBe("007 is a spy");
  });
});
