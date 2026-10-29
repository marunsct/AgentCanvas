import { expect, test } from "./fixtures";

// Sprint 26: a model block reuses answers to repeated requests, and the workflow settings show what its runs spent
// today and this month against a day budget.

test("cache a model's answers, run twice, and see the spending against a day budget", async ({ page }) => {
  const request = `Where is my order ${Date.now()}?`; // the cache is shared and kept, so a repeat of an old run would be cached already
  await page.goto("/");
  await page.getByTestId("template-classify_and_route").click();
  await page.getByTestId("node-fast_model").click();
  await expect(page.locator("legend", { hasText: "Backup models" })).toBeVisible();
  await page.getByLabel("Reuse answers for (s)").fill("600");
  await expect(page.locator(".save-state")).toHaveText("Saved");

  await page.getByRole("tab", { name: "Run" }).click();
  for (const expected of [/tok/, /2 cached/]) {
    await page.getByLabel("run input").fill(JSON.stringify({ request }));
    await page.getByRole("button", { name: "▶ Run" }).click();
    await expect(page.getByTestId("run-usage")).toContainText(expected, { timeout: 15_000 });
  }

  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByRole("tab", { name: "Inspector" }).click();
  await page.locator("summary", { hasText: /^Budget$/ }).click();
  await page.getByLabel("budget USD per day (all runs)").fill("5");
  await page.getByLabel("budget alert at percent").fill("50, 90");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await page.getByLabel("spending").getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByTestId("spend-day")).toContainText("of $5.00");
  await expect(page.getByTestId("spend-day")).toContainText("2 runs");
  await expect(page.getByTestId("spend-day")).toContainText("2 cached answers");
  await expect(page.getByTestId("spend-month")).toContainText("This month");
});
