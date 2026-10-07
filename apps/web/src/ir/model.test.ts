import { describe, expect, it } from "vitest";
import { manifests, triage } from "../test/fixtures";
import { defaultsFromSchema, routesOf, uniqueName } from "./model";

describe("model helpers", () => {
  it("uniqueName makes snake_case identifiers without collisions", () => {
    expect(uniqueName("Ask AI", [])).toBe("ask_ai");
    expect(uniqueName("Ask AI", ["ask_ai", "ask_ai_2"])).toBe("ask_ai_3");
    expect(uniqueName("2nd step", [])).toBe("n_2nd_step");
    expect(uniqueName("End", [])).toBe("end_2");
  });

  it("routesOf lists cases plus the default route", () => {
    expect(routesOf(triage().nodes.find((n) => n.id === "n_rt")!)).toEqual(["high", "normal"]);
  });

  it("defaultsFromSchema takes top-level defaults", () => {
    expect(defaultsFromSchema(manifests.get("core.router")!.config_schema as Record<string, unknown>)).toEqual({ default: "default" });
  });
});
