import { expect, test } from "./fixtures";

// Sprint 22: export a whole workflow to another framework through Agent Spec (preview), seeing the gaps first.

test("pick another framework in the export, read what does not carry over, download", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", {
    data: { name: `Bridge ${Date.now()}`, template: "classify_and_route" },
  })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByTestId("export-open").click();

  await page.locator("#export-framework").selectOption("wayflow");
  const gaps = page.getByTestId("bridge-gaps");
  await expect(gaps).toContainText("Preview.");
  await expect(gaps).toContainText("Nothing stops this workflow from running there.");
  await expect(gaps).toContainText("nothing reports back to AgentCanvas");
  await expect(page.getByLabel("Where it will run")).toHaveCount(0);

  // OpenAI Agents SDK flows take no steps AgentCanvas runs itself
  await page.locator("#export-framework").selectOption("openai_agents");
  await expect(gaps).toContainText("Before it runs there");
  await expect(gaps).toContainText("'apologise' is a step AgentCanvas runs itself");

  await page.locator("#export-framework").selectOption("langgraph");
  const download = page.waitForEvent("download");
  await page.getByTestId("export-download").click();
  expect((await download).suggestedFilename()).toMatch(/-langgraph\.zip$/);
});
