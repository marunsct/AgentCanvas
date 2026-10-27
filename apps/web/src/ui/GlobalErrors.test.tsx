// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { describeFailure, GlobalErrors } from "./GlobalErrors";

afterEach(cleanup);

describe("describeFailure", () => {
  it("says what went wrong in the person's words, and stays quiet about what is not a failure", () => {
    expect(describeFailure(new Error("disk full"))).toBe("disk full");
    expect(describeFailure(new ApiError(409, { title: "Conflict", detail: "the draft changed" }))).toBe("the draft changed");
    expect(describeFailure("plain text")).toBe("plain text");
    expect(describeFailure(new DOMException("stopped", "AbortError"))).toBeNull();
    expect(describeFailure(new ApiError(401, { title: "Unauthorized" }))).toBeNull(); // sign-in takes over
    expect(describeFailure(new Error("ResizeObserver loop completed with undelivered notifications."))).toBeNull();
    expect(describeFailure(undefined)).toBeNull();
    expect(describeFailure(new Error("x".repeat(500)))?.length).toBe(301);
  });
});

describe("GlobalErrors", () => {
  it("shows a banner for a rejected promise nobody handled, and the person can dismiss it", () => {
    render(<GlobalErrors />);
    expect(screen.queryByTestId("global-error")).toBeNull();
    act(() => {
      const e = new Event("unhandledrejection") as Event & { reason: unknown };
      e.reason = new ApiError(500, { title: "Internal error", detail: "something broke on the server" });
      window.dispatchEvent(e);
    });
    expect(screen.getByRole("alert").textContent).toContain("something broke on the server");
    act(() => screen.getByRole("button", { name: "Dismiss" }).click());
    expect(screen.queryByTestId("global-error")).toBeNull();
  });
});
