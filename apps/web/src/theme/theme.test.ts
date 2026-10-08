// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, resolved, useTheme } from "./theme";

describe("themes", () => {
  beforeEach(() => localStorage.clear());

  it("applies the chosen theme to the page and remembers it", () => {
    useTheme.getState().setTheme("medium");
    expect(document.documentElement.dataset.theme).toBe("medium");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(localStorage.getItem("agentcanvas.theme")).toBe("medium");
    useTheme.getState().setTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("resolves system to light or dark", () => {
    expect(["light", "dark"]).toContain(resolved("system"));
    applyTheme("system");
    expect(["light", "dark"]).toContain(document.documentElement.dataset.theme);
  });
});
