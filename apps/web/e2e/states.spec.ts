import { expect, test } from "@playwright/test";

// Sprint 9 (M6-T8): screens explain failures and empty states instead of staying blank.

test("an API failure shows an error with Retry, which recovers", async ({ page }) => {
  let fail = true;
  await page.route("**/api/v1/projects/*/workflows*", (route) =>
    fail ? route.fulfill({ status: 503, json: { title: "Service unavailable", detail: "database down" } }) : route.fallback(),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Could not load workflows: the server failed (503): database down", { timeout: 15_000 });
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("an unknown page and an unknown workflow explain themselves", async ({ page }) => {
  await page.goto("/nope");
  await expect(page.getByText("Page not found")).toBeVisible();
  await page.goto("/workflows/wf_does_not_exist");
  await expect(page.getByText("Could not load workflow")).toBeVisible();
});
