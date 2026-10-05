import { afterEach, describe, expect, it, vi } from "vitest";
import { accessToken, initAuth } from "./auth";
import { apiFetch } from "./client";

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
});
