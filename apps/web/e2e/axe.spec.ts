import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, SETTINGS, test } from "./fixtures";

// An automated accessibility scan (axe-core, WCAG 2.1 A and AA) of every page. It finds what a machine can: names,
// roles, contrast, landmarks, duplicate ids. Whether the product is pleasant with a screen reader is for a person.

const problems: string[] = [];

async function scan(page: Page, where: string) {
  await page.waitForLoadState("load");
  await page.waitForTimeout(800); // placeholders appear a moment after load, and pulse (their contrast changes)
  await expect(page.locator(".skeleton")).toHaveCount(0, { timeout: 10_000 });
  await page.waitForTimeout(300);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  for (const v of result.violations) {
    problems.push(`${where}: ${v.id} (${v.impact}): ${v.help} - ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  }
}

test.beforeEach(() => { problems.length = 0; });

test("the pages outside the editor have no machine-detectable accessibility problems", async ({ page }) => {
  for (const path of ["/", "/workspace", "/inbox", "/memory", "/events", "/playground", "/knowledge", "/runs", ...SETTINGS]) {
    await page.goto(path);
    await scan(page, path);
  }
  expect(problems).toEqual([]);
});

test("the editor, its panels and the evaluation page have none either", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-classify")).toBeVisible();
  await scan(page, "the editor");
  for (const tab of ["Inspector", "Copilot", "Run", "Tests", "Versions", "Code"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await scan(page, `the editor's ${tab} tab`);
  }
  const wfPath = new URL(page.url()).pathname;
  await page.goto(`${wfPath}/evaluation`);
  await scan(page, "the evaluation page");
  expect(problems).toEqual([]);
});

test.describe("dark theme", () => {
  test.use({ colorScheme: "dark" });
  test("the main pages and the editor have none in the dark theme", async ({ page }) => {
    for (const path of ["/", "/inbox", "/knowledge", "/runs", "/settings/general", "/settings/members"]) {
      await page.goto(path);
      await scan(page, `${path} (dark)`);
    }
    await page.goto("/");
    await page.getByTestId("template-support_triage").click();
    await expect(page.getByTestId("node-classify")).toBeVisible();
    await scan(page, "the editor (dark)");
    await page.getByRole("tab", { name: "Code", exact: true }).click();
    await scan(page, "the Code tab (dark)");
    expect(problems).toEqual([]);
  });
});
