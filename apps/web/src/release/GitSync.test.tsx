// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const link: Record<string, string | number | null> = {
  repo: "acme/agents", branch: "agentcanvas/support", folder: "agents/support", token_secret: "GITHUB_TOKEN", target: "local",
  auto: "publish", pr_number: 7, pr_url: "https://github.com/acme/agents/pull/7", last_version: "v1.2.0", last_error: null,
};
const state = { link: link as typeof link | null, pull: "conflict" as "conflict" | "applied" };
const calls: string[] = [];

vi.mock("../api/client", () => {
  class ApiError extends Error {
    constructor(public status: number, public problem: { detail?: string }) { super(problem.detail); }
  }
  return {
    API_BASE: "/api/v1",
    ApiError,
    apiFetch: vi.fn(async (path: string, init: { method?: string; json?: { overwrite?: boolean } } = {}) => {
      calls.push(`${init.method ?? "GET"} ${path}${init.json?.overwrite ? " overwrite" : ""}`);
      if (path.endsWith("/git") && !init.method) return state.link;
      if (path.endsWith("/git/pull")) {
        if (state.pull === "conflict" && !init.json?.overwrite) throw new ApiError(409, { detail: "the draft and the branch have both changed" });
        return { outcome: "applied", version: 4, summary: "1 step changed" };
      }
      if (path.endsWith("/git/push")) {
        return { pr_url: link.pr_url, pr_number: 7, pr_created: false, changed: true, note: null };
      }
      return state.link;
    }),
  };
});
vi.mock("../api/session", () => ({ can: () => true }));
vi.mock("../editor/store", () => ({ useEditor: { getState: () => ({ flush: async () => undefined, load: async () => undefined }) } }));

const { GitSync } = await import("./GitSync");

afterEach(() => { cleanup(); calls.length = 0; state.link = link; state.pull = "conflict"; });
const mount = () => render(
  <QueryClientProvider client={new QueryClient()}><GitSync wfId="wf_1" version="1.2.0" target="local" /></QueryClientProvider>,
);

describe("Git round trip (G-14)", () => {
  it("connects a workflow to a branch", async () => {
    state.link = null;
    mount();
    fireEvent.change(await screen.findByLabelText("Repository"), { target: { value: "acme/agents" } });
    fireEvent.click(screen.getByTestId("git-connect"));
    await waitFor(() => expect(calls).toContain("PUT /workflows/wf_1/git"));
  });

  it("opens a pull request for the chosen version and links it", async () => {
    mount();
    expect((await screen.findByTestId("git-pr")).getAttribute("href")).toBe(link.pr_url);
    fireEvent.click(screen.getByTestId("git-push"));
    expect((await screen.findByTestId("git-pushed")).textContent).toContain("pull request #7");
    expect(screen.getByTestId("git-push").textContent).toContain("v1.2.0");
  });

  it("asks before replacing the draft when both sides changed", async () => {
    mount();
    fireEvent.click(await screen.findByTestId("git-pull"));
    expect((await screen.findByTestId("git-conflict")).textContent).toContain("both changed");
    expect(calls).not.toContain("POST /workflows/wf_1/git/pull overwrite");
    fireEvent.click(screen.getByRole("button", { name: /Import anyway/ }));
    expect((await screen.findByTestId("git-pulled")).textContent).toContain("1 step changed");
    expect(calls).toContain("POST /workflows/wf_1/git/pull overwrite");
  });

  it("shows why the last sync failed", async () => {
    state.link = { ...link, last_error: "GitHub did not accept the token" };
    mount();
    expect((await screen.findByTestId("git-last-error")).textContent).toContain("did not accept the token");
  });
});
