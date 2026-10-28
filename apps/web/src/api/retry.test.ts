import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { shouldRetry } from "./retry";

describe("shouldRetry", () => {
  it("asks once more after a dropped connection or a server error, never after a client error", () => {
    expect(shouldRetry(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(shouldRetry(0, new ApiError(503, { title: "Unavailable" }))).toBe(true);
    expect(shouldRetry(0, new ApiError(0, { title: "No answer" }))).toBe(true);
    expect(shouldRetry(0, new ApiError(404, { title: "Not found" }))).toBe(false);
    expect(shouldRetry(0, new ApiError(403, { title: "Forbidden" }))).toBe(false);
    expect(shouldRetry(0, new ApiError(422, { title: "Invalid" }))).toBe(false);
    expect(shouldRetry(1, new TypeError("Failed to fetch"))).toBe(false);
  });
});
