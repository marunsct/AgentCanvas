// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { ShortcutKey } from "./ShortcutKey";

afterEach(cleanup);

describe("? key", () => {
  it("opens the keyboard shortcut sheet, but not while typing", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ShortcutKey />
        <Routes>
          <Route path="/" element={<input aria-label="name" />} />
          <Route path="/settings/shortcuts" element={<p>the shortcuts</p>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.keyDown(screen.getByLabelText("name"), { key: "?" }); // typing a question mark
    expect(screen.queryByText("the shortcuts")).toBeNull();
    fireEvent.keyDown(document.body, { key: "?", ctrlKey: true });
    expect(screen.queryByText("the shortcuts")).toBeNull();
    fireEvent.keyDown(document.body, { key: "?" });
    expect(screen.getByText("the shortcuts")).toBeTruthy();
  });
});
