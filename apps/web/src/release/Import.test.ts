import { describe, expect, it } from "vitest";
import { nameFromFile } from "./Import";

describe("nameFromFile", () => {
  it("reads a workflow name from the file name", () => {
    expect(nameFromFile("customer-support.flow.json")).toBe("Customer support");
    expect(nameFromFile("My_n8n_workflow.json")).toBe("My n8n workflow");
    expect(nameFromFile(".json")).toBe("");
  });
});
