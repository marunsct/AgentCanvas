import { afterEach, describe, expect, it, vi } from "vitest";
import { chooseProject, PROJECT_ID } from "./queries";
import { can, canOrg, ME, useMe } from "./session";

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

afterEach(() => {
  vi.unstubAllGlobals();
  useMe.setState({ me: { id: "usr_dev", email: "", org_id: "org_default", role: "admin", memberships: {} }, loaded: false });
});

describe("the role in the project (E-10)", () => {
  it("is the chosen project's, while the organisation's pages keep the organisation role", async () => {
    useMe.setState({ me: { id: "usr_x", email: "", org_id: "org_x", role: "viewer", memberships: { org_x: "viewer" } }, loaded: true });
    vi.stubGlobal("localStorage", { getItem: () => "prj_b", setItem: () => {}, removeItem: () => {} });
    vi.stubGlobal("fetch", vi.fn(async () => json([
      { id: "prj_a", name: "A", role: "viewer" }, { id: "prj_b", name: "B", role: "builder" },
    ])));
    await chooseProject();
    expect(PROJECT_ID).toBe("prj_b");
    expect(ME.project_role).toBe("builder");
    expect([can("builder"), can("admin")]).toEqual([true, false]);
    expect([canOrg("viewer"), canOrg("builder")]).toEqual([true, false]);
  });

  it("says why when there is no project to see and no right to make one", async () => {
    useMe.setState({ me: { id: "usr_x", email: "", org_id: "org_x", role: "viewer", memberships: { org_x: "viewer" } }, loaded: true });
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {}, removeItem: () => {} });
    vi.stubGlobal("fetch", vi.fn(async () => json([])));
    await expect(chooseProject()).rejects.toThrow(/no project you can see yet; ask an admin to add you/);
  });
});
