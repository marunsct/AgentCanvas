import { expect, test } from "@playwright/test";

// The walking-skeleton journey on the real editor: template -> run -> tool approval -> complete; reject on a new thread.
test("run → approve → complete, then reject on a new thread", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("node-reply_drafter")).toHaveClass(/status-interrupted/);
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("the tool call finished (demo model)", { timeout: 10_000 });
  await expect(page.getByTestId("node-reply_drafter")).toHaveClass(/status-ok/);

  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeHidden({ timeout: 10_000 });
  expect(errors).toEqual([]);
});
