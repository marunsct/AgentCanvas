import { answerDialog, expect, test } from "./fixtures";

// E-10: a project's own members, with a role there, and its own spending caps, from Settings › Project.

test("add someone to the project, change their role there, remove them", async ({ page }) => {
  const email = `pia-${Date.now()}@example.com`;
  await page.request.put("/api/v1/org/members", { data: { email, role: "viewer" } }); // in the organisation first
  await page.goto("/");
  await page.getByTestId("settings-link").click();
  await page.getByTestId("settings-project-members").click();
  await page.getByTestId("project-member-who").fill(email);
  await page.getByLabel("role", { exact: true }).selectOption("builder");
  await page.getByRole("button", { name: "Add" }).click();
  const row = page.getByTestId("project-members").locator("tr", { hasText: email });
  await expect(row.getByLabel(`role of ${email}`)).toHaveValue("builder");
  await row.getByLabel(`role of ${email}`).selectOption("reviewer");
  await expect(row.getByLabel(`role of ${email}`)).toHaveValue("reviewer");
  await row.getByRole("button", { name: "Remove" }).click();
  await answerDialog(page);
  await expect(row).toHaveCount(0);
});

test("someone outside the organisation cannot be added to a project", async ({ page }) => {
  await page.goto("/settings/project-members");
  await page.getByTestId("project-member-who").fill(`nobody-${Date.now()}@example.com`);
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("alert")).toContainText("is not a member of the organisation");
});

test("the project's spending caps are set from its settings", async ({ page }) => {
  await page.goto("/settings/project-spending");
  const day = page.getByLabel("Cap a day (USD)");
  await day.fill("25");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Saved the spending caps of/)).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Cap a day (USD)")).toHaveValue("25");
  await page.getByLabel("Cap a day (USD)").fill(""); // no cap again, for the other tests
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Saved the spending caps of/).first()).toBeVisible();
});
