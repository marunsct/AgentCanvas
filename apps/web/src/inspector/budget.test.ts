import { describe, expect, it } from "vitest";
import { parseAlertShares } from "./budget";

describe("cost alert shares (S26)", () => {
  it("reads percents as shares, sorted and without repeats", () => {
    expect(parseAlertShares("80, 50 50%")).toEqual([0.5, 0.8]);
    expect(parseAlertShares("  ")).toBeNull();
    expect(parseAlertShares("120")).toBeUndefined();
    expect(parseAlertShares("eighty")).toBeUndefined();
  });
});
