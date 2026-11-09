import { describe, expect, it } from "vitest";
import { tokenRoles, tokenState } from "./ApiTokens";

describe("API tokens", () => {
  it("offers roles up to the person's own", () => {
    expect(tokenRoles("viewer")).toEqual(["viewer"]);
    expect(tokenRoles("builder")).toEqual(["viewer", "reviewer", "builder"]);
    expect(tokenRoles("admin")).toHaveLength(4);
  });

  it("tells active, expired and revoked tokens apart", () => {
    const now = Date.parse("2026-10-29T12:00:00Z");
    expect(tokenState({ revoked_at: null, expires_at: null }, now)).toBe("active");
    expect(tokenState({ revoked_at: null, expires_at: "2026-10-30T00:00:00Z" }, now)).toBe("active");
    expect(tokenState({ revoked_at: null, expires_at: "2026-10-29T11:59:59Z" }, now)).toBe("expired");
    expect(tokenState({ revoked_at: "2026-10-28T00:00:00Z", expires_at: null }, now)).toBe("revoked");
  });
});
