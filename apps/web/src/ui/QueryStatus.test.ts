import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { errorText } from "./QueryStatus";

describe("errorText", () => {
  it("explains network failures and server errors in plain words", () => {
    expect(errorText(new TypeError("Failed to fetch"))).toBe("the API cannot be reached — is the server running?");
    expect(errorText(new ApiError(503, { detail: "database down" }))).toBe("the server failed (503): database down");
    expect(errorText(new ApiError(404, { detail: "workflow 'x' not found" }))).toBe("workflow 'x' not found");
  });
});
