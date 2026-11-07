// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const silent = vi.fn();
let stored: { access_token: string; expired: boolean } | null = null;

vi.mock("oidc-client-ts", () => ({
  WebStorageStateStore: class {},
  UserManager: class {
    events = { addUserLoaded: () => {}, addUserUnloaded: () => {} };
    getUser = async () => stored;
    signinSilent = silent;
    signinRedirect = async () => {};
  },
}));

import { accessToken, initAuth, renew } from "./auth";
import { authFetch } from "./client";

afterEach(() => { vi.unstubAllGlobals(); silent.mockReset(); });

const bearer = (init: RequestInit) => (init.headers as Headers).get("Authorization");

describe("token renewal", () => {
  it("many 401s at once share one silent renewal", async () => {
    stored = { access_token: "old", expired: false };
    expect(await initAuth({ mode: "oidc", issuer: "https://idp.test", client_id: "web" })).toBeNull();
    let finish!: (u: unknown) => void;
    silent.mockImplementation(() => new Promise((ok) => { finish = ok; }));
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) =>
      new Response("{}", { status: bearer(init) === "Bearer new" ? 200 : 401 })));
    const calls = [authFetch("/a"), authFetch("/b"), authFetch("/c")];
    await vi.waitFor(() => expect(silent).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 10));
    finish({ access_token: "new", expired: false });
    expect((await Promise.all(calls)).map((r) => r.status)).toEqual([200, 200, 200]);
    expect(silent).toHaveBeenCalledTimes(1);
    // once settled, a later renewal starts afresh
    silent.mockResolvedValue({ access_token: "newer", expired: false });
    expect(await renew()).toBe(true);
    expect(silent).toHaveBeenCalledTimes(2);
  });

  it("renews an expired token before sending instead of earning a 401", async () => {
    stored = { access_token: "x", expired: false };
    await initAuth({ mode: "oidc", issuer: "https://idp.test", client_id: "web" });
    stored.expired = true; // the token ran out while the page sat open
    expect(accessToken()).toBeNull();
    silent.mockResolvedValue({ access_token: "fresh", expired: false });
    const fetchMock = vi.fn(async (_u: string, _init: RequestInit) => new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await authFetch("/a");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bearer(fetchMock.mock.calls[0]![1])).toBe("Bearer fresh");
  });
});
