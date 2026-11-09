import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  accessToken: vi.fn(() => "t"),
  renew: vi.fn(async () => true),
  signInAgain: vi.fn(() => true),
  stepUp: vi.fn(() => true),
  tokenExpired: vi.fn(() => false),
}));
vi.mock("./auth", () => auth);

const { apiFetch, ApiError } = await import("./client");

const mfa = (acr: string | null) => new Response(JSON.stringify({
  type: "https://agentcanvas.dev/errors/mfa-required", status: 401, detail: "sign in again with a second factor", acr_values: acr,
}), {
  status: 401,
  headers: {
    "content-type": "application/problem+json",
    "www-authenticate": `Bearer error="insufficient_user_authentication", acr_values="${acr ?? ""}"`,
  },
});

beforeEach(() => { for (const f of Object.values(auth)) f.mockClear(); });
afterEach(() => vi.unstubAllGlobals());

describe("a 401 that asks for multi-factor sign-in (E-8)", () => {
  it("signs in again at the level the API names, without renewing the same session first", async () => {
    const fetch = vi.fn(async () => mfa("urn:mfa"));
    vi.stubGlobal("fetch", fetch);
    const err = await apiFetch("/projects").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as InstanceType<typeof ApiError>).message).toContain("second factor");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(auth.renew).not.toHaveBeenCalled();
    expect(auth.stepUp).toHaveBeenCalledWith("urn:mfa");
    expect(auth.signInAgain).not.toHaveBeenCalled();
  });

  it("a plain 401 still renews, then signs in again", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await apiFetch("/projects").catch(() => {});
    expect(auth.renew).toHaveBeenCalledTimes(1);
    expect(auth.signInAgain).toHaveBeenCalledTimes(1);
    expect(auth.stepUp).not.toHaveBeenCalled();
  });
});
