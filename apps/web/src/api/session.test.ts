import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({ apiFetch: vi.fn() }));
vi.mock("./auth", () => ({ portalUrl: () => null }));

import { apiFetch } from "./client";
import { can, loadMe, ME, useMe } from "./session";

describe("session state", () => {
  beforeEach(() => vi.mocked(apiFetch).mockReset());

  it("starts as the dev admin until /me answers", () => {
    expect(useMe.getState().loaded).toBe(false);
    expect(can("admin")).toBe(true);
  });

  it("loadMe updates the store and the ME binding together", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ id: "u1", email: "a@b.c", org_id: "o1", role: "viewer", memberships: {} });
    await loadMe();
    expect(useMe.getState().loaded).toBe(true);
    expect(ME.id).toBe("u1");
    expect(can("builder")).toBe(false);
    expect(can("viewer")).toBe(true);
  });

  it("subscribers hear about a role change", async () => {
    const seen: string[] = [];
    const off = useMe.subscribe((s) => seen.push(s.me.role));
    vi.mocked(apiFetch).mockResolvedValue({ id: "u1", email: "", org_id: "o2", role: "builder", memberships: {} });
    await loadMe();
    off();
    expect(seen).toEqual(["builder"]);
  });
});
