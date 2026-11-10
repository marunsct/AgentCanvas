// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeploymentOut } from "../api/generated";
import type { DeploymentKeyOut } from "../api/generated";

const calls: { path: string; method?: string; json?: unknown }[] = [];
let keys: DeploymentKeyOut[] = [];
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string; json?: Record<string, unknown> }) => {
    calls.push({ path, method: init?.method, json: init?.json });
    if (init?.method === "POST") {
      const made = { ...key("k_new", String(init.json?.name)), api_key: "ac_secret_value" };
      keys = [made, ...keys];
      return made;
    }
    if (init?.method === "DELETE") {
      keys = keys.map((k) => (path.endsWith(k.id) ? { ...k, revoked_at: "2026-11-10T00:00:00Z", active: false } : k));
      return undefined;
    }
    return keys;
  }),
}));
const confirm = vi.fn(async () => true);
vi.mock("../ui/dialog", () => ({ confirmDialog: () => confirm(), toast: () => {} }));

const { DeploymentKeys } = await import("./DeploymentKeys");

function key(id: string, name: string, extra: Partial<DeploymentKeyOut> = {}): DeploymentKeyOut {
  return {
    id, name, prefix: `ac_${id}`.slice(0, 10), created_by: "usr_dev", created_at: "2026-11-01T00:00:00Z",
    expires_at: null, last_used_at: null, revoked_at: null, active: true, ...extra,
  };
}
const deployment = { id: "d1", environment: "prod" } as DeploymentOut;

function show(onNewKey = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><DeploymentKeys deployment={deployment} onNewKey={onNewKey} /></QueryClientProvider>);
  return onNewKey;
}

beforeEach(() => {
  calls.length = 0;
  confirm.mockClear();
  keys = [
    key("k_ci", "CI", { last_used_at: new Date().toISOString(), expires_at: "2099-01-01T00:00:00Z" }),
    key("k_old", "old partner", { revoked_at: "2026-11-02T00:00:00Z", active: false }),
  ];
});
afterEach(cleanup);

describe("DeploymentKeys (E-8)", () => {
  it("lists the working keys, and the revoked ones on request", async () => {
    show();
    expect(await screen.findByText("CI")).toBeTruthy();
    expect(screen.getByText("API keys (1 working)")).toBeTruthy();
    expect(screen.queryByText("old partner")).toBeNull();
    fireEvent.click(screen.getByText("Show revoked and expired keys (1)"));
    expect(screen.getByText("old partner")).toBeTruthy();
    expect(screen.getByText("revoked")).toBeTruthy();
  });

  it("adds a named key with an expiry and hands its value up, shown once", async () => {
    const onNewKey = show();
    await screen.findByText("CI");
    fireEvent.change(screen.getByLabelText("key name"), { target: { value: " website " } });
    fireEvent.change(screen.getByLabelText("key expiry"), { target: { value: "90" } });
    await act(async () => { fireEvent.click(screen.getByText("Add key")); });
    expect(calls.find((c) => c.method === "POST")).toEqual({ path: "/deployments/d1/keys", method: "POST", json: { name: "website", expires_in_days: 90 } });
    expect(onNewKey).toHaveBeenCalledWith("ac_secret_value");
    expect(await screen.findByText("website")).toBeTruthy();
    expect(screen.queryByText("ac_secret_value")).toBeNull(); // the list never shows a key itself
  });

  it("revokes one key after asking", async () => {
    show();
    await screen.findByText("CI");
    await act(async () => { fireEvent.click(screen.getByLabelText("revoke CI")); });
    expect(confirm).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/deployments/d1/keys/k_ci")).toBe(true));
    expect(await screen.findByText("API keys (0 working)")).toBeTruthy();
  });
});
