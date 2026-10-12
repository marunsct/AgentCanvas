import { describe, expect, it } from "vitest";
import { parseAlertShares } from "./budget";

describe("cost alert shares (S26)", () => {
  it("reads percents as shares, sorted and without repeats", () => {
    expect(parseAlertShares("80, 50 50%")).toEqual([0.5, 0.8]);
    expect(parseAlertShares("  ")).toBeNull();
    expect(parseAlertShares("120")).toBeUndefined();
    expect(parseAlertShares("eighty")).toBeUndefined();
  });

  it("allows at most 5 alerts, counted after removing repeats", () => {
    expect(parseAlertShares("10, 20, 30, 40, 50, 50")).toEqual([0.1, 0.2, 0.3, 0.4, 0.5]);
    expect(parseAlertShares("10, 20, 30, 40, 50, 60")).toBeUndefined();
  });
});
