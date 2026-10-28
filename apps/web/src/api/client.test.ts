import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "./client";

describe("apiFetch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("gives up on a server that never answers, with an error a person can read", async () => {
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_ok, fail) => {
      init.signal?.addEventListener("abort", () => fail(init.signal!.reason));
    })));
    const err = (await apiFetch("/slow", { timeoutMs: 20 }).catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/did not answer/);
  });

  it("leaves a caller's own abort as an abort", async () => {
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_ok, fail) => {
      init.signal?.addEventListener("abort", () => fail(init.signal!.reason));
    })));
    const stop = new AbortController();
    const pending = apiFetch("/slow", { signal: stop.signal }).catch((e) => e);
    stop.abort();
    expect(await pending).not.toBeInstanceOf(ApiError);
  });

  it("reads a 204 and a plain-text answer", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    expect(await apiFetch("/x", { method: "DELETE" })).toBeUndefined();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("print(1)", { headers: { "content-type": "text/plain" } })));
    expect(await apiFetch<string>("/code")).toBe("print(1)");
  });

  it("does not hand a web page to a component as if it were data", async () => {
    // a proxy that answers /api with the app's own index.html (200) is the commonest deployment mistake
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<!doctype html><html></html>", { headers: { "content-type": "text/html; charset=utf-8" } })));
    await expect(apiFetch("/projects/p/workflows")).rejects.toMatchObject({
      status: 200,
      message: expect.stringContaining("web page instead of data"),
    });
  });

  it("explains an HTML error page from a proxy by its status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })));
    await expect(apiFetch("/x")).rejects.toMatchObject({ status: 502, message: "HTTP 502" });
  });
});
