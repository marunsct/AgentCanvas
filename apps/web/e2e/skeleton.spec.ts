import { expect, test } from "@playwright/test";

// M0-T4: the walking skeleton — run, nodes light up, approve, complete; a second run can be rejected.
test("run → approve → complete, then reject on a new thread", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto("/");
  await expect(page.getByText("Support triage")).toBeVisible();

  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByText("Approval needed")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Reply sent.")).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByText("Approval needed")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Approval needed")).toBeHidden({ timeout: 10_000 });
  expect(errors).toEqual([]);
});
