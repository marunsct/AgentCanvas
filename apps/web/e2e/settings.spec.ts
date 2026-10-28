import { answerDialog, expect, runnableWorkflow, test } from "./fixtures";

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

test("default models by purpose: set the judges' model and see what each purpose uses", async ({ page }) => {
  const project = await (await page.request.post("/api/v1/projects", { data: { name: `Models ${Date.now()}` } })).json();
  await page.addInitScript((id) => localStorage.setItem("agentcanvas.project", id), project.id);
  await page.goto("/settings/models");
  const card = page.getByRole("region", { name: "Models by purpose" });
  await expect(card).toContainText("choose a model for judge evaluators");
  await card.getByLabel("default model for Judge evaluators").fill("anthropic:claude-haiku-4-5");
  await card.getByRole("button", { name: "Save default models" }).click();
  await expect(page.getByTestId("toasts")).toContainText("Saved the default models");
  await expect(card).toContainText("the project's default for judge evaluators");
  await page.request.delete(`/api/v1/projects/${project.id}`);
});

test("the workflow list renames, duplicates and deletes, and finds by name", async ({ page }) => {
  const stamp = Date.now();
  await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Listed ${stamp}` } });
  await page.goto("/");
  await page.getByLabel("search workflows").fill(`Listed ${stamp}`);
  const list = page.getByTestId("workflow-list");
  await expect(list.locator("li")).toHaveCount(1);

  await page.getByTestId(`workflow-menu-Listed ${stamp}`).click();
  await page.getByRole("menuitem", { name: "Rename…" }).click();
  await answerDialog(page, `Renamed ${stamp}`);
  await page.getByLabel("search workflows").fill(`${stamp}`);
  await expect(list).toContainText(`Renamed ${stamp}`);

  await page.getByTestId(`workflow-menu-Renamed ${stamp}`).click();
  await page.getByRole("menuitem", { name: "Duplicate" }).click();
  await expect(page).toHaveURL(/\/workflows\/wf_/);
  await page.goto("/");
  await page.getByLabel("search workflows").fill(`${stamp}`);
  await expect(list.locator("li")).toHaveCount(2);

  await page.getByTestId(`workflow-menu-Renamed ${stamp} (copy)`).click();
  await page.getByRole("menuitem", { name: "Delete…" }).click();
  await answerDialog(page);
  await expect(list.locator("li")).toHaveCount(1);
});

test("Runs lists a workflow's conversations and opens one in the editor", async ({ page }) => {
  const wf = await runnableWorkflow(page.request, `Ran ${Date.now()}`);
  const ran = await page.request.post(`/api/v1/workflows/${wf.id}/runs/stream`, { data: { input: {} } });
  expect(ran.ok()).toBeTruthy();
  await page.goto("/runs");
  await page.getByLabel("filter by workflow").selectOption(wf.id);
  const row = page.getByTestId("runs").locator("tbody tr").first();
  await expect(row).toContainText(wf.name);
  await expect(row).toContainText("Editor");
  await row.getByRole("link", { name: wf.name }).click();
  await expect(page).toHaveURL(new RegExp(`/workflows/${wf.id}\\?thread=th_`));
});

test("the editor's History tab lists this workflow's runs and opens one in the Timeline", async ({ page }) => {
  const wf = await runnableWorkflow(page.request, `History ${Date.now()}`);
  const ran = await page.request.post(`/api/v1/workflows/${wf.id}/runs/stream`, { data: { input: {} } });
  const thread = ran.headers()["x-thread-id"];
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "History", exact: true }).click();
  const row = page.getByTestId("run-history").locator("tbody tr").first();
  await expect(row).toContainText("Editor");
  await row.click();
  await expect(page.getByRole("tab", { name: "Timeline", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(thread).toMatch(/^th_/);
});

test("on a 1024-px laptop the top bar keeps Settings and the person menu in sight; page names stay readable", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto("/runs");
  for (const id of ["settings-link", "me", "events-link"]) {
    const box = await page.getByTestId(id).boundingBox();
    expect(box && box.x + box.width, id).toBeLessThanOrEqual(1024);
  }
  await expect(page.getByRole("link", { name: "Events" })).toHaveAttribute("title", "Events"); // shown on hover
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings/);
});

test("a workflow of another project (from the Inbox, Runs or a link) opens in its own project", async ({ page }) => {
  const stamp = Date.now();
  const project = await (await page.request.post("/api/v1/projects", { data: { name: `Elsewhere ${stamp}` } })).json();
  const wf = await (await page.request.post(`/api/v1/projects/${project.id}/workflows`, { data: { name: `Away ${stamp}` } })).json();
  await page.goto("/");
  await expect(page.getByTestId("project-name")).not.toHaveText(`Elsewhere ${stamp}`);
  await page.goto(`/workflows/${wf.id}`);
  await expect(page).toHaveURL(new RegExp(`/workflows/${wf.id}$`));
  await expect(page.locator(".crumb")).toContainText(`Away ${stamp}`);
  await page.getByRole("link", { name: "AgentCanvas" }).first().click();
  await expect(page.getByTestId("project-name")).toHaveText(`Elsewhere ${stamp}`);
  expect((await page.request.delete(`/api/v1/projects/${project.id}`)).ok()).toBeTruthy();
});
