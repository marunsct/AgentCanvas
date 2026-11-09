import { expect, test } from "./fixtures";

// The user guide in the app: opened from the person menu, its links move between pages and land on the section.

test("the user guide opens from the person menu, and its links lead to a page's section", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("me").click();
  await page.getByTestId("guide-link").click();
  await expect(page).toHaveURL(/\/guide$/);
  const guide = page.getByTestId("guide");
  await expect(guide.locator("h1")).toBeVisible();

  await page.getByRole("navigation", { name: "guide pages" }).getByRole("link", { name: "Building on the canvas" }).click();
  await expect(page).toHaveURL(/\/guide\/canvas$/);
  await guide.getByRole("link", { name: "Models" }).first().click(); // models.md#backup-models-and-reusing-answers
  await expect(page).toHaveURL(/\/guide\/models#backup-models-and-reusing-answers$/);
  await expect(page.locator("#backup-models-and-reusing-answers")).toBeInViewport();
  await expect(guide.locator("table").first()).toBeVisible(); // the providers table renders as a table

  await page.goto("/guide/nope");
  await expect(guide).toContainText(/Could not load|not found|no guide page/i);
});
