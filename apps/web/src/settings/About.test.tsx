// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import pkg from "../../package.json";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, apiFetch }));

const { About } = await import("./About");

afterEach(() => { cleanup(); apiFetch.mockReset(); });

async function renderIt() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><About /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByTestId("about-server").textContent).not.toBe("…"));
}

describe("Settings › About", () => {
  it("shows the server's release and commit, and the web app's", async () => {
    apiFetch.mockResolvedValue({ version: pkg.version, commit: "0123456789abcdef0123" });
    await renderIt();
    expect(apiFetch).toHaveBeenCalledWith("/version");
    expect(screen.getByTestId("about-server").textContent).toBe(`${pkg.version} (commit 0123456789ab)`);
    expect(screen.getByTestId("about-web").textContent).toContain(pkg.version);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so when the web app and the server are different releases", async () => {
    apiFetch.mockResolvedValue({ version: "0.1.0", commit: null });
    await renderIt();
    expect(screen.getByTestId("about-server").textContent).toBe("0.1.0");
    expect(screen.getByRole("alert").textContent).toContain("different releases");
  });
});
