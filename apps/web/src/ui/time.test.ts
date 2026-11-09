import { describe, expect, it } from "vitest";
import { ago } from "./time";

describe("ago", () => {
  it("says how long ago in words", () => {
    const now = Date.parse("2026-10-28T12:00:00Z");
    const before = (s: number) => new Date(now - s * 1000).toISOString();
    expect(ago(before(10), now)).toBe("just now");
    expect(ago(before(60), now)).toBe("a minute ago");
    expect(ago(before(5 * 60), now)).toBe("5 min ago");
    expect(ago(before(3 * 3600), now)).toBe("3 hours ago");
    expect(ago(before(30 * 3600), now)).toBe("yesterday");
    expect(ago(before(3 * 86400), now)).toBe("3 days ago");
    expect(ago(before(40 * 86400), now)).toMatch(/\d/);
    expect(ago("not a date", now)).toBe("");
  });
});
