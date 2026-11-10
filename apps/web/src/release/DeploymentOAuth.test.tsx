// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeploymentOut, OAuthOut } from "../api/generated";

const calls: { path: string; method?: string; json?: unknown }[] = [];
let state: OAuthOut;
let refuse: string | null = null;
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string; json?: Record<string, unknown> }) => {
    calls.push({ path, method: init?.method, json: init?.json });
    if (init?.method === "PUT") {
      if (refuse) throw new Error(refuse);
      const body = init.json ?? {};
      state = body.enabled
        ? { ...state, enabled: true, issuer: (body.issuer as string | null) ?? null, audience: (body.audience as string | null) ?? null,
            scopes: body.scopes as string[], authorization_server: (body.issuer as string | null) ?? "https://login.example.com" }
        : { ...state, enabled: false };
      return state;
    }
    return state;
  }),
}));

const { DeploymentOAuth, DEFAULT_SCOPE } = await import("./DeploymentOAuth");
const deployment = { id: "d1", environment: "prod" } as DeploymentOut;

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><DeploymentOAuth deployment={deployment} /></QueryClientProvider>);
}

beforeEach(() => {
  calls.length = 0;
  refuse = null;
  state = {
    enabled: false, issuer: null, authorization_server: "https://login.example.com", audience: null, scopes: [DEFAULT_SCOPE],
    mcp_resource: "https://canvas.example.com/api/v1/deployments/d1/mcp", a2a_resource: "https://canvas.example.com/api/v1/deployments/d1/a2a",
    mcp_metadata: "https://canvas.example.com/.well-known/oauth-protected-resource/api/v1/deployments/d1/mcp",
    a2a_metadata: "https://canvas.example.com/.well-known/oauth-protected-resource/api/v1/deployments/d1/a2a",
    ag_ui_resource: "https://canvas.example.com/api/v1/deployments/d1/ag-ui", api_resource: "https://canvas.example.com/api/v1/deployments/d1",
    ag_ui_metadata: "https://canvas.example.com/.well-known/oauth-protected-resource/api/v1/deployments/d1/ag-ui",
    api_metadata: "https://canvas.example.com/.well-known/oauth-protected-resource/api/v1/deployments/d1",
  };
});
afterEach(cleanup);

describe("DeploymentOAuth (E-11)", () => {
  it("is off until turned on, and then shows what the identity provider needs", async () => {
    show();
    expect(await screen.findByText("Turn on")).toBeTruthy();
    expect(screen.getByText(/the identity provider people sign in to AgentCanvas with \(https:\/\/login\.example\.com\)/)).toBeTruthy();
    expect(screen.queryByTestId("oauth-mcp-resource")).toBeNull();
    fireEvent.change(screen.getByTestId("oauth-audience"), { target: { value: " api://agentcanvas " } });
    fireEvent.change(screen.getByTestId("oauth-scopes"), { target: { value: `${DEFAULT_SCOPE}  agents` } });
    await act(async () => { fireEvent.click(screen.getByText("Turn on")); });
    expect(calls.find((c) => c.method === "PUT")).toEqual({
      path: "/deployments/d1/oauth", method: "PUT",
      json: { enabled: true, issuer: null, audience: "api://agentcanvas", scopes: [DEFAULT_SCOPE, "agents"] },
    });
    expect((await screen.findByTestId("oauth-mcp-resource")).textContent).toBe(state.mcp_resource);
    expect(screen.getByTestId("oauth-ag-ui-resource").textContent).toBe(state.ag_ui_resource);
    expect(screen.getByTestId("oauth-api-resource").textContent).toBe(state.api_resource);
    expect(screen.getByText("Sign-in with OAuth: on")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("People can now sign in");
  });

  it("names another issuer, shows a refusal, and turns off", async () => {
    state = { ...state, enabled: true };
    show();
    await screen.findByText("Turn off");
    fireEvent.change(screen.getByTestId("oauth-issuer"), { target: { value: "https://idp.example.org" } });
    refuse = "cannot load the signing keys of https://idp.example.org";
    await act(async () => { fireEvent.click(screen.getByText("Save")); });
    expect((await screen.findByRole("alert")).textContent).toContain("signing keys");
    refuse = null;
    await act(async () => { fireEvent.click(screen.getByText("Turn off")); });
    expect(calls.filter((c) => c.method === "PUT").at(-1)?.json).toEqual({ enabled: false });
    expect(await screen.findByText("Turn on")).toBeTruthy();
  });
});
