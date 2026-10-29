// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Menu } from "./Menu";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Menu", () => {
  it("moves a left-aligned menu opened near the window's right edge back inside it", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1000 });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const left = parseFloat(this.style.left || "0");
      return this.getAttribute("role") === "menu"
        ? ({ left, right: left + 300, width: 300, top: 40, bottom: 400 } as DOMRect)
        : ({ left: 900, right: 960, width: 60, top: 10, bottom: 30 } as DOMRect);
    });
    render(<Menu label="Model" testId="m" align="left">{() => <button type="button">Claude Opus 5.5</button>}</Menu>);
    fireEvent.click(screen.getByTestId("m"));
    expect(screen.getByRole("menu").style.left).toBe("692px"); // 1000 - 8 - 300, not 900
  });
});
