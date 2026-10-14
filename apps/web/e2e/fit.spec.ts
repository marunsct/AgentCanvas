import { expect, test } from "./fixtures";

// After the roadmap: the export writes a workflow for any installed framework, a non-LangGraph workflow's export
// shows no deploy-kit error, and the editor's top bar fits a laptop window in Full mode.

test("export a workflow for another framework, and a CrewAI workflow without the deploy kit", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", {
    data: { name: `Any framework ${Date.now()}`, template: "classify_and_route" },
  })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByTestId("export-open").click();
  await expect(page.getByLabel("Where it will run")).toBeVisible();

  await page.locator("#export-framework").selectOption("fw:crewai");
  await expect(page.getByLabel("Where it will run")).toHaveCount(0);
  await expect(page.getByTestId("export-framework-note")).toContainText("A CrewAI project");
  const download = page.waitForEvent("download");
  await page.getByTestId("export-download").click();
  expect((await download).suggestedFilename()).toMatch(/-crewai[-.]/); // the server names the file

  // a workflow designed for CrewAI: its own project, and no "deploy kit builds LangGraph services" error
  const crew = await (await api.post("/api/v1/projects/prj_default/workflows", {
    data: { name: `Crew ${Date.now()}`, template: "classify_and_route" },
  })).json();
  await api.put(`/api/v1/workflows/${crew.id}`, { data: { ir: { ...crew.ir, framework: "crewai" } }, headers: { "If-Match": "1" } });
  await page.goto(`/workflows/${crew.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByTestId("export-open").click();
  await expect(page.getByTestId("export-framework-note")).toContainText("A CrewAI project");
  await expect(page.getByTestId("export-box").getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("the deploy kit builds LangGraph services")).toHaveCount(0);
});

test("the top bar fits a 1280-pixel window in Full mode", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: `Fit ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("full-toggle").click();
  const bar = page.locator(".topbar");
  await expect(page.getByRole("button", { name: "▶ Test" })).toBeInViewport();
  expect(await bar.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.getByTestId("more-menu").click();
  await expect(page.getByTestId("workspace-link")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("workspace-link")).toHaveCount(0);
});
