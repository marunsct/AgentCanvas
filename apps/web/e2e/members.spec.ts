import { answerDialog, expect, test } from "./fixtures";

// Sprint 10 (M7-T3): an admin invites someone by email and changes their role.

test("invite a member, change their role, remove them", async ({ page }) => {
  const email = `pat-${Date.now()}@example.com`;
  await page.goto("/");
  await page.getByTestId("settings-link").click();
  await page.getByTestId("settings-members").click();
  await page.getByLabel("email").fill(email);
  await page.getByLabel("role", { exact: true }).selectOption("viewer");
  await page.getByRole("button", { name: "Invite" }).click();
  const row = page.getByTestId("members").locator("tr", { hasText: email });
  await expect(row).toContainText("invited");
  await row.getByLabel(`role of ${email}`).selectOption("reviewer");
  await expect(row.getByLabel(`role of ${email}`)).toHaveValue("reviewer");
  await row.getByRole("button", { name: "Remove" }).click();
  await answerDialog(page);
  await expect(row).toHaveCount(0);
});

test("the audit log shows who changed a member", async ({ page }) => {
  const email = `audit-${Date.now()}@example.com`;
  await page.request.put("/api/v1/org/members", { data: { email, role: "viewer" } });
  await page.goto("/");
  await page.getByTestId("settings-link").click();
  await page.getByTestId("settings-audit").click();
  await page.getByLabel("action").fill("set_member");
  await expect(page.getByTestId("audit").locator("tbody tr").first()).toContainText("dev@localhost");
  await expect(page.getByTestId("audit").locator("tbody tr").first()).toContainText("Set member");
});
