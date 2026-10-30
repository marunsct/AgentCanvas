// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const calls: { path: string; init?: { method?: string; json?: unknown } }[] = [];
let admin = true;
vi.mock("../api/session", () => ({ useCan: () => admin }));
vi.mock("../ui/dialog", () => ({ toast: vi.fn(), confirmDialog: vi.fn(async () => true) }));
vi.mock("../api/client", () => ({
  apiFetch: vi.fn(async (path: string, init?: { method?: string; json?: unknown }) => {
    calls.push({ path, init });
    if (path === "/connections") return [];
    if (path === "/connections/clients" && !init?.method) {
      return { redirect_uri: "https://canvas.test/oauth/callback", clients: [{ issuer: "https://as.test", host: "as.test", client_id: "gh_1", has_secret: true, created_at: "2026-10-30T00:00:00Z" }] };
    }
    return {};
  }),
}));

const { ConnectionsSettings } = await import("./ConnectionsSettings");
const show = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ConnectionsSettings /></QueryClientProvider>);

beforeEach(() => { calls.length = 0; admin = true; });
afterEach(cleanup);

describe("Connections › client credentials (N-5)", () => {
  it("shows the redirect address and the entered credentials, never the secret", async () => {
    show();
    expect(await screen.findByText("gh_1")).toBeTruthy();
    expect(screen.getByText("kept")).toBeTruthy();
    expect(document.body.textContent).toContain("https://canvas.test/oauth/callback");
  });

  it("saves a client id and secret for a connector's server", async () => {
    show();
    await screen.findByText("gh_1");
    fireEvent.change(screen.getByLabelText("connector server address"), { target: { value: "https://mcp.example.com/mcp" } });
    fireEvent.change(screen.getByLabelText("client id"), { target: { value: "abc" } });
    fireEvent.change(screen.getByLabelText("client secret"), { target: { value: "shh-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(put.path).toBe("/connections/clients");
    expect(put.init?.json).toEqual({ server_url: "https://mcp.example.com/mcp", client_id: "abc", client_secret: "shh-secret" });
  });

  it("removes credentials by their sign-in server", async () => {
    show();
    fireEvent.click(await screen.findByRole("button", { name: "remove the credentials for as.test" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.init?.method === "DELETE")!.path).toBe("/connections/clients?issuer=https%3A%2F%2Fas.test");
  });

  it("is for admins only", async () => {
    admin = false;
    show();
    await screen.findByText("No connections yet.");
    expect(screen.queryByLabelText("client id")).toBeNull();
    expect(calls.some((c) => c.path === "/connections/clients")).toBe(false);
  });
});
