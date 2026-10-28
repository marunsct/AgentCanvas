import { expect, test as base } from "@playwright/test";

// Every journey fails when the page breaks the Content-Security-Policy (S12): under the production policy in CI
// that catches a library that starts to need eval or inline script.
export const test = base.extend<{ cspGuard: void }>({
  cspGuard: [
    async ({ page }, use) => {
      // the beginner guide opens by itself on a first visit; journeys start as returning users (help.spec.ts covers it)
      await page.addInitScript(() => { try { localStorage.setItem("agentcanvas.helpSeen", "1"); } catch { /* ignore */ } });
      const violations: string[] = [];
      page.on("console", (m) => {
        if (/Content Security Policy|Refused to (evaluate|execute|load)/i.test(m.text())) violations.push(m.text());
      });
      await use();
      expect(violations, "Content-Security-Policy violations").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Answer the app's own dialog (ui/dialog.tsx): type `text` into its field when it has one, then confirm. */
export async function answerDialog(page: import("@playwright/test").Page, text?: string): Promise<void> {
  const dialog = page.getByTestId("dialog");
  await expect(dialog).toBeVisible();
  if (text !== undefined) await dialog.getByRole("textbox").fill(text);
  await dialog.locator("button.confirm").click();
  await expect(dialog).toHaveCount(0);
}

/** Every section of the Settings page (settings/SettingsPage.tsx), for sweeps that visit every page. */
export const SETTINGS = ["general", "models", "copilot", "secrets", "sandbox", "observability", "organisation", "members", "policies", "audit", "server", "appearance", "shortcuts"].map((s) => `/settings/${s}`);

/** A workflow with one step (`out = "Hello {{ state.who }}"`), so it can be run; a new workflow has no steps and is refused. */
export async function runnableWorkflow(api: import("@playwright/test").APIRequestContext, name: string): Promise<{ id: string; name: string }> {
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name } })).json();
  const ir = {
    ...wf.ir, state: { channels: [{ name: "who" }, { name: "out" }] },
    nodes: [{ id: "n_g", type: "core.set", name: "greet", config: { values: { out: "Hello {{ state.who }}" } } }],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_g" } },
      { id: "e2", kind: "flow", source: { node: "n_g" }, target: { node: "END" } },
    ],
  };
  expect((await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir }, headers: { "If-Match": "1" } })).ok()).toBeTruthy();
  return wf;
}
