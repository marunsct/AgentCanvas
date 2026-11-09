// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const posts: string[] = [];
vi.mock("../api/client", () => ({
  API_BASE: "/api/v1",
  ApiError: class extends Error {},
  apiFetch: vi.fn(async (path: string, init?: { method?: string }) => {
    if (init?.method === "POST") {
      posts.push(path);
      return { callback_secret: "whsec_shown_once" };
    }
    if (path.endsWith("/deployments")) return [{ id: "d1", environment: "prod", semver: "1.0.0", active: true, key_prefix: "ac_x" }];
    if (path === "/org/settings") return { offered_environments: ["prod"] };
    return [];
  }),
}));
vi.mock("../api/session", () => ({ can: () => true, useCan: () => true, ME: { role: "builder" } }));
vi.mock("./ConnectSection", () => ({ ConnectSection: () => null }));
vi.mock("./ShareSection", () => ({ ShareSection: () => null }));
const answer = vi.fn(async () => true);
vi.mock("../ui/dialog", () => ({ confirmDialog: (...a: unknown[]) => answer(...(a as [])), promptDialog: vi.fn() }));

const { PublishSection } = await import("./PublishSection");
afterEach(cleanup);

it("creates a deployment's callback secret after asking, and shows it once", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PublishSection wfId="wf_1" />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "Callback secret" });
  answer.mockResolvedValueOnce(false);
  await act(async () => { fireEvent.click(button); });
  expect(posts).toEqual([]); // the person said no: the secret in use keeps signing
  await act(async () => { fireEvent.click(button); });
  expect(posts).toEqual(["/deployments/d1/callback-secret"]);
  expect((await screen.findByTestId("callback-secret")).textContent).toContain("whsec_shown_once");
});
