// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useThrottled } from "./useThrottled";

describe("useThrottled", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(1_000_000); });
  afterEach(() => vi.useRealTimers());

  it("shows the first value at once, then at most one change per interval, and always the last", () => {
    const { result, rerender } = renderHook(({ v }) => useThrottled(v, 100), { initialProps: { v: "a" } });
    expect(result.current).toBe("a");
    rerender({ v: "ab" }); // right after the first: held back
    expect(result.current).toBe("a");
    rerender({ v: "abc" });
    rerender({ v: "abcd" });
    act(() => { vi.advanceTimersByTime(99); });
    expect(result.current).toBe("a");
    act(() => { vi.advanceTimersByTime(2); });
    expect(result.current).toBe("abcd"); // the latest, not each step between
  });

  it("shows a change that comes after a quiet period at once", () => {
    const { result, rerender } = renderHook(({ v }) => useThrottled(v, 100), { initialProps: { v: 1 } });
    act(() => { vi.advanceTimersByTime(500); });
    rerender({ v: 2 });
    expect(result.current).toBe(2);
  });
});
