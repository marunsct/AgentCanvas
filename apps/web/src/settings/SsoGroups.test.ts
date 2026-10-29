import { describe, expect, it } from "vitest";
import { rowsToRoles } from "./SsoGroups";

describe("SSO group table", () => {
  it("drops blank groups, trims names and keeps the last role of a repeated group", () => {
    expect(rowsToRoles([
      { group: " eng ", role: "builder" },
      { group: "", role: "admin" },
      { group: "ops", role: "viewer" },
      { group: "ops", role: "admin" },
    ])).toEqual({ eng: "builder", ops: "admin" });
  });
});
