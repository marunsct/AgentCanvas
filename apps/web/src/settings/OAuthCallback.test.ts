import { describe, expect, it } from "vitest";
import { readCallback } from "./OAuthCallback";

describe("OAuth callback address", () => {
  it("reads the code, the state and the issuer", () => {
    expect(readCallback("?code=abc&state=xyz&iss=https%3A%2F%2Fas.test")).toEqual({ code: "abc", state: "xyz", iss: "https://as.test" });
    expect(readCallback("?code=abc&state=xyz")).toEqual({ code: "abc", state: "xyz" });
  });

  it("reports the server's error, or a missing code", () => {
    expect(readCallback("?error=access_denied&error_description=You%20said%20no")).toEqual({ error: "access_denied: You said no" });
    expect(readCallback("?state=xyz")).toHaveProperty("error");
    expect(readCallback("")).toHaveProperty("error");
  });
});
