// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./QueryStatus";

afterEach(cleanup);

function Boom({ error }: { error: Error }): never {
  throw error;
}

describe("ErrorBoundary", () => {
  it("names the crash, and for data of an unexpected shape suggests that the page and the server differ", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined); // React and the boundary log the crash
    render(<ErrorBoundary label="This page"><Boom error={new TypeError("x.map is not a function")} /></ErrorBoundary>);
    const box = screen.getByRole("alert");
    expect(box.textContent).toContain("This page crashed: x.map is not a function");
    expect(box.textContent).toContain("different versions");
    cleanup();
    render(<ErrorBoundary><Boom error={new Error("deliberate")} /></ErrorBoundary>);
    expect(screen.getByRole("alert").textContent).toContain("This view crashed: deliberate");
    expect(screen.getByRole("alert").textContent).not.toContain("different versions");
  });
});
