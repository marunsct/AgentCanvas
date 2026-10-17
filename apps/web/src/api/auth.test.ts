import { afterEach, describe, expect, it, vi } from "vitest";
import { accessToken, initAuth, maturaMode, portalUrl, signInAgain, signInEnabled } from "./auth";
import { apiFetch, authFetch } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("sign-in", () => {
  it("dev mode needs no sign-in and sends no token", async () => {
    expect(await initAuth({ mode: "dev" })).toBeNull();
    expect(accessToken()).toBeNull();
    const fetchMock = vi.fn(async () => new Response("[]", { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await apiFetch("/projects");
    const headers = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].headers as Headers;
    expect(headers.has("Authorization")).toBe(false);
  });

  it("refuses an OIDC configuration without a client", async () => {
    await expect(initAuth({ mode: "oidc", issuer: "https://idp.test" })).rejects.toThrow(/no OIDC client/);
  });

  it("Matura mode signs in like OIDC and knows its portal", async () => {
    await expect(initAuth({ mode: "matura", issuer: "https://matura.test/realms/matura", portal_url: "https://portal.test" }))
      .rejects.toThrow(/no OIDC client/);
    expect(maturaMode()).toBe(true);
    expect(portalUrl()).toBe("https://portal.test");
    expect(await initAuth({ mode: "dev" })).toBeNull();
    expect(maturaMode()).toBe(false);
    expect(portalUrl()).toBeNull();
  });

  it("dev mode never redirects on 401 and does not retry", async () => {
    expect(signInEnabled()).toBe(false);
    expect(signInAgain()).toBe(false);
    const fetchMock = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await authFetch("/api/v1/me")).status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
