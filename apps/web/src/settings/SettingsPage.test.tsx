// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "../app/AppShell";
import { useMe, type Role } from "../api/session";
import { SettingsPage } from "./SettingsPage";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { headers: { "content-type": "application/json" } })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function as(role: Role) {
  useMe.setState({ me: { id: "usr_x", email: "pat@example.com", org_id: "org_x", role, memberships: { org_x: role } }, loaded: true });
}

function at(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/settings/:section?" element={<SettingsPage />} />
            <Route path="/" element={<p>home</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Settings", () => {
  it("lists the audit log for admins only, and opens the first section when none is named", async () => {
    as("admin");
    await act(async () => { at("/settings"); });
    expect(screen.getByTestId("settings-section-general")).toBeTruthy();
    expect(screen.getByTestId("settings-audit")).toBeTruthy();
    cleanup();
    as("viewer");
    await act(async () => { at("/settings/audit"); }); // a section the role cannot read: back to the first one
    expect(screen.queryByTestId("settings-audit")).toBeNull();
    expect(screen.getByTestId("settings-section-general")).toBeTruthy();
  });

  it("is reached from the top bar, which shows a viewer no Playground", async () => {
    as("viewer");
    await act(async () => { at("/settings/appearance"); });
    expect(screen.getByTestId("settings-link").className).toContain("active");
    expect(screen.queryByTestId("playground-link")).toBeNull();
    expect(screen.getByRole("radiogroup", { name: "Colour theme" })).toBeTruthy();
    cleanup();
    as("builder");
    await act(async () => { at("/"); });
    expect(screen.getByTestId("playground-link")).toBeTruthy();
  });
});
