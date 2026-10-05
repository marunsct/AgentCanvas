import { expect, test } from "@playwright/test";

// Sprint 10 (M7-T3): an admin invites someone by email and changes their role.

test("invite a member, change their role, remove them", async ({ page }) => {
  const email = `pat-${Date.now()}@example.com`;
  await page.goto("/");
  await page.getByTestId("members-link").click();
  await page.getByLabel("email").fill(email);
  await page.getByLabel("role", { exact: true }).selectOption("viewer");
  await page.getByRole("button", { name: "Invite" }).click();
  const row = page.getByTestId("members").locator("tr", { hasText: email });
  await expect(row).toContainText("invited");
  await row.getByLabel(`role of ${email}`).selectOption("reviewer");
  await expect(row.getByLabel(`role of ${email}`)).toHaveValue("reviewer");
  page.once("dialog", (d) => void d.accept());
  await row.getByRole("button", { name: "Remove" }).click();
  await expect(row).toHaveCount(0);
});
