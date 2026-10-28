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
export const SETTINGS = ["general", "models", "copilot", "secrets", "observability", "members", "policies", "audit", "appearance"].map((s) => `/settings/${s}`);
