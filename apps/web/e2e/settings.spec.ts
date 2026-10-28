import { answerDialog, expect, test } from "./fixtures";

// The app shell and Settings: make a project from the top bar's project menu, rename it in Settings › General,
// then delete it (typing its name) and land in another project. Uses a project of its own.

test("create, rename and delete a project from the shell and Settings", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/");
  await page.getByTestId("project-switcher").click();
  await page.getByTestId("new-project").click();
  await answerDialog(page, `Shell ${stamp}`);
  await expect(page.getByTestId("project-name")).toHaveText(`Shell ${stamp}`);

  await page.getByTestId("settings-link").click();
  await expect(page).toHaveURL(/\/settings\/general$/);
  await page.getByLabel("project name").fill(`Renamed ${stamp}`);
  await page.getByRole("button", { name: "Rename" }).click();
  await expect(page.getByTestId("project-name")).toHaveText(`Renamed ${stamp}`);

  await page.getByTestId("delete-project").click();
  await answerDialog(page, `Renamed ${stamp}`);
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("project-name")).not.toHaveText(`Renamed ${stamp}`);
  const names = (await (await page.request.get("/api/v1/projects")).json()).map((p: { name: string }) => p.name);
  expect(names).not.toContain(`Renamed ${stamp}`);
});

test("the top bar reaches every page, and the old addresses lead into Settings", async ({ page }) => {
  await page.goto("/");
  for (const [link, url] of [["knowledge-link", /\/knowledge$/], ["memory-link", /\/memory$/], ["events-link", /\/events$/],
    ["inbox-link", /\/inbox$/], ["workspace-link", /\/workspace$/], ["workflows-link", /\/$/]] as const) {
    await page.getByTestId(link).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByTestId(link)).toHaveClass(/active/);
  }
  for (const [old, now] of [["/setup", "models"], ["/members", "members"], ["/audit", "audit"], ["/policies", "policies"]]) {
    await page.goto(old);
    await expect(page).toHaveURL(new RegExp(`/settings/${now}$`));
  }
});
