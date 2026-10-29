import { describe, expect, it } from "vitest";
import { scimBaseUrl } from "./ScimSettings";

describe("SCIM settings", () => {
  it("shows the address the identity provider reaches, behind the web container's /api", () => {
    expect(scimBaseUrl("https://canvas.example.com", "/scim/v2")).toBe("https://canvas.example.com/api/scim/v2");
    expect(scimBaseUrl("https://canvas.example.com/", "/scim/v2")).toBe("https://canvas.example.com/api/scim/v2");
  });
});
