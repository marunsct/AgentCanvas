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

test("the sandbox limits are a form, and the network list is offered only where it is enforced", async ({ page }) => {
  await page.goto("/settings/sandbox");
  // the key-less dev server runs workspace code in-process: no network list, and the page says why
  await expect(page.getByTestId("egress-not-enforced")).toContainText("cannot limit which hosts");
  await expect(page.getByLabel("host to allow")).toHaveCount(0);
  await page.getByLabel("Time limit per call (seconds)").fill("45");
  await page.getByRole("button", { name: "Save sandbox settings" }).click();
  await expect(page.getByTestId("toasts")).toContainText("Saved the sandbox settings");
  const file = await (await page.request.get("/api/v1/projects/prj_default/files/sandbox.json")).json();
  expect(JSON.parse(file.content).timeout_s).toBe(45);
  await page.request.put("/api/v1/projects/prj_default/sandbox", { data: {} }); // back to the defaults for other journeys
});
