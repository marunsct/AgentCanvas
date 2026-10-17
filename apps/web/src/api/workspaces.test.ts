import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initAuth } from "./auth";
import { chooseProject, chooseWorkspace, PROJECT_ID, PROJECT_NAME, WORKSPACE_ID, WORKSPACES } from "./queries";
import { ME, orgPortalUrl } from "./session";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const WS = [{ id: "w-main", name: "Main", is_default: true }, { id: "w-lab", name: "Lab", is_default: false }];

function storage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
}

beforeEach(async () => {
  vi.stubGlobal("localStorage", storage());
  // Matura mode without a client: initAuth refuses, but the mode and portal are set (enough for these helpers)
  await initAuth({ mode: "matura", issuer: "https://id.test/realms/matura", portal_url: "https://id.test/portal/" }).catch(() => undefined);
});
afterEach(async () => {
  vi.unstubAllGlobals();
  await initAuth({ mode: "dev" });
});

describe("Matura workspaces (TS26)", () => {
  it("works in the picked workspace's project and makes one when it has none", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/workspaces")) return json(WS);
      if (url.endsWith("/projects")) {
        return init?.method === "POST"
          ? json({ id: "prj_lab", name: JSON.parse(String(init.body)).name }, 201)
          : json([{ id: "prj_any", name: "Default project" }]);
      }
      if (url.includes("workspace_id=w-lab")) return json([]);
      return json([]);
    }));
    await chooseProject();
    expect(WORKSPACES.map((w) => w.name)).toEqual(["Main", "Lab"]);
    expect([WORKSPACE_ID, PROJECT_ID, PROJECT_NAME]).toEqual([null, "prj_any", "Default project"]);

    expect(await chooseWorkspace("w-lab")).toBe("prj_lab");
    expect([WORKSPACE_ID, PROJECT_NAME]).toEqual(["w-lab", "Lab project"]);
    expect(calls).toContain("POST /api/v1/projects");
    await chooseProject(); // the choice is remembered
    expect(WORKSPACE_ID).toBe("w-lab");
  });

  it("goes back to the previous workspace when the new one cannot be used", async () => {
    const saved = ME.role;
    ME.role = "viewer";
    try {
      vi.stubGlobal("fetch", vi.fn(async (url: string) =>
        url.endsWith("/workspaces") ? json(WS) : url.includes("workspace_id=w-lab") ? json([]) : json([{ id: "prj_a", name: "A" }])));
      await chooseProject();
      await expect(chooseWorkspace("w-lab")).rejects.toThrow(/workspace 'Lab' has no projects/);
      expect([WORKSPACE_ID, PROJECT_ID]).toEqual([null, "prj_a"]);
    } finally {
      ME.role = saved;
    }
  });

  it("links to the organisation's page in Matura's portal", () => {
    const saved = { ...ME };
    try {
      ME.org_id = "t-1";
      ME.portal_url = null;
      expect(orgPortalUrl("members")).toBe("https://id.test/portal/t-1/members");
      ME.portal_url = "https://id.test/portal/acme/";
      expect(orgPortalUrl("members")).toBe("https://id.test/portal/acme/members");
      expect(orgPortalUrl()).toBe("https://id.test/portal/acme/");
    } finally {
      Object.assign(ME, saved);
    }
  });
});
