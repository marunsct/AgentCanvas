import { describe, expect, it, vi } from "vitest";
import type { KeyboardEvent } from "react";
import { isActivate, onActivate } from "./keys";

const ev = (key: string) => ({ key, preventDefault: vi.fn(), stopPropagation: vi.fn() }) as unknown as KeyboardEvent;

describe("keyboard activation", () => {
  it("accepts Enter and Space only", () => {
    expect(isActivate({ key: "Enter" })).toBe(true);
    expect(isActivate({ key: " " })).toBe(true);
    expect(isActivate({ key: "a" })).toBe(false);
  });
  it("runs and swallows the key on activation", () => {
    const fn = vi.fn();
    const e = ev(" ");
    onActivate(fn)(e);
    expect(fn).toHaveBeenCalledOnce();
    expect(e.preventDefault).toHaveBeenCalled();
  });
  it("ignores other keys", () => {
    const fn = vi.fn();
    onActivate(fn)(ev("Tab"));
    expect(fn).not.toHaveBeenCalled();
  });
});
