// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Link, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary, isChunkLoadError } from "../ui/QueryStatus";
import { RouteBoundary } from "./AppShell";

afterEach(cleanup);

function Boom({ error }: { error: Error }): never {
  throw error;
}

describe("page error boundary", () => {
  it("shows the next page after one crashed", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <MemoryRouter initialEntries={["/broken"]}>
        <Link to="/fine">elsewhere</Link>
        <RouteBoundary label="This page">
          <Routes>
            <Route path="/broken" element={<Boom error={new Error("deliberate")} />} />
            <Route path="/fine" element={<p>fine page</p>} />
          </Routes>
        </RouteBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert").textContent).toContain("This page crashed: deliberate");
    fireEvent.click(screen.getByText("elsewhere"));
    expect(screen.getByText("fine page")).toBeTruthy(); // the crash used to stay until a reload
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says a page that could not be downloaded needs a reload, not that it crashed", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const chunk = new TypeError("Failed to fetch dynamically imported module: https://x/assets/EditorPage-abc.js");
    expect(isChunkLoadError(chunk)).toBe(true);
    expect(isChunkLoadError(new TypeError("x.map is not a function"))).toBe(false);
    render(<ErrorBoundary label="This page"><Boom error={chunk} /></ErrorBoundary>);
    const box = screen.getByRole("alert");
    expect(box.textContent).toContain("could not be downloaded");
    expect(box.textContent).not.toContain("crashed");
    expect(box.textContent).not.toContain("different versions");
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
