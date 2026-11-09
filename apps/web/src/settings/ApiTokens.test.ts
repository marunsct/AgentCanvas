import { describe, expect, it } from "vitest";
import { expiryChoices, tokenRoles, tokenState } from "./ApiTokens";

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

describe("token lifetimes", () => {
  it("offers only lifetimes within the organisation's longest", () => {
    expect(expiryChoices(0).map(([, n]) => n)).toEqual([30, 90, 365, null]); // no cap: as before
    expect(expiryChoices(90).map(([, n]) => n)).toEqual([30, 90]);
    expect(expiryChoices(60)).toEqual([["30 days", 30], ["60 days", 60]]);
    expect(expiryChoices(7)).toEqual([["7 days", 7]]);
  });
});
