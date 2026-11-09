// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const redirects: unknown[] = [];
vi.mock("oidc-client-ts", () => ({
  WebStorageStateStore: class {},
  UserManager: class {
    events = { addUserLoaded: () => {}, addUserUnloaded: () => {} };
    getUser = async () => ({ access_token: "t", expired: false });
    signinRedirect = async (args: unknown) => { redirects.push(args); };
    signoutRedirect = async () => {};
    removeUser = async () => {};
  },
}));

const { initAuth, signOut, stepUp, switchAccount, switchOrganisation } = await import("./auth");

function seed() {
  sessionStorage.clear();
  sessionStorage.setItem("agentcanvas.playground:org_1:usr_a:prj_1", "[{\"role\":\"user\",\"content\":\"secret\"}]");
  sessionStorage.setItem("agentcanvas.view.wf_1", "{}");
  sessionStorage.setItem("agentcanvas.signin.redirected_at", "1");
  sessionStorage.setItem("oidc.user:https://idp.test:web", "{}");
}

const left = () => Object.keys(sessionStorage).sort();

describe("leaving a session", () => {
  beforeEach(() => { redirects.length = 0; });

  it.each([
    ["signing out", () => signOut()],
    ["switching account", () => switchAccount()],
    ["switching organisation", () => switchOrganisation("org_2")],
  ])("%s forgets what the app kept in this tab (Playground conversation, views)", async (_name, leave) => {
    await initAuth({ mode: "matura", issuer: "https://idp.test", client_id: "web" });
    seed();
    await leave();
    // the redirect guard stays (it stops a sign-in loop); the identity provider's own entries are its business
    expect(left()).toEqual(["agentcanvas.signin.redirected_at", "oidc.user:https://idp.test:web"]);
  });
});

describe("a step-up sign-in (E-8)", () => {
  beforeEach(() => { redirects.length = 0; sessionStorage.clear(); });

  it("asks the identity provider to sign in afresh at the level the API needs, once a minute at most", async () => {
    await initAuth({ mode: "oidc", issuer: "https://idp.test", client_id: "web" });
    expect(stepUp("urn:mfa gold")).toBe(true);
    expect(redirects).toEqual([{ state: "/", prompt: "login", acr_values: "urn:mfa gold" }]);
    expect(stepUp("urn:mfa gold")).toBe(false); // the provider still did not do it: show the error, no loop
    sessionStorage.clear();
    expect(stepUp(null)).toBe(true);
    expect(redirects[1]).toEqual({ state: "/", prompt: "login" });
  });
});
