import { describe, expect, it } from "vitest";
import { manifests } from "../test/fixtures";
import { diffToMergePatch, estimateTokens, uiSchemaFromFormats } from "./schema";

describe("inspector schema helpers", () => {
  it("maps formats to widgets, including array items and additionalProperties", () => {
    const router = uiSchemaFromFormats(manifests.get("core.router")!.config_schema as Record<string, unknown>);
    expect(router).toEqual({ cases: { items: { when: { "ui:widget": "cel" } } } });
    const set = uiSchemaFromFormats(manifests.get("core.set")!.config_schema as Record<string, unknown>);
    expect(set).toEqual({ values: { additionalProperties: { "ui:widget": "template" } } });
  });

  it("diffToMergePatch nulls removed keys and keeps only changes", () => {
    expect(diffToMergePatch({ a: 1, b: [1], c: "x" }, { a: 1, b: [1, 2] })).toEqual({ b: [1, 2], c: null });
  });

  it("estimates tokens", () => expect(estimateTokens("12345678")).toBe(2));
});
