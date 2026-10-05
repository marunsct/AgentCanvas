import { describe, expect, it } from "vitest";
import { can, ME } from "./session";

describe("can", () => {
  it("ranks roles viewer < reviewer < builder < admin", () => {
    const saved = ME.role;
    try {
      ME.role = "reviewer";
      expect([can("viewer"), can("reviewer"), can("builder"), can("admin")]).toEqual([true, true, false, false]);
      ME.role = "admin";
      expect(can("builder")).toBe(true);
    } finally {
      ME.role = saved;
    }
  });
});
