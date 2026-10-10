import { expect, test } from "./fixtures";

// Sprint 27: a workflow designed for CrewAI becomes a native CrewAI flow (read in the Code tab) and runs; a Crew
// block (CrewAI only) runs a team of agents as one step.

test("switch a template to CrewAI, read its flow, run it", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-classify_and_route").click();
  await expect(page.getByTestId("node-classify")).toBeVisible();
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByLabel("framework", { exact: true }).selectOption("crewai");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^CrewAI: ready/ })).toBeVisible();

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".code-view")).toContainText("from crewai.flow import");
  await expect(page.locator(".code-view")).toContainText("@router(classify)");

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"request": "Where is my order?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("reply", { timeout: 30_000 });
  await expect(page.getByTestId("node-classify")).toHaveClass(/status-ok/);
});

test("a Crew block runs a team of agents as one step", async ({ page }) => {
  const api = page.request;
  const model = { id: "r_model", type: "model.chat", name: "model", config: { model: "anthropic:claude-haiku-4-5" } };
  const crew = { id: "n_crew", type: "crewai.crew", name: "research",
    config: {
      agents: [{ name: "researcher", role: "Researcher", goal: "Find facts about {{ state.topic }}" }],
      tasks: [{ description: "List facts about {{ state.topic }}", expected_output: "Three facts" }],
      process: "sequential", output: "report",
    } };
  const ir = {
    name: `Crew ${Date.now()}`, framework: "crewai",
    state: { channels: [{ name: "topic", default: "" }, { name: "report", default: "" }] },
    resources: [model], nodes: [crew],
    edges: [
      { id: "e1", kind: "flow", source: { node: "START" }, target: { node: "n_crew" } },
      { id: "e2", kind: "flow", source: { node: "n_crew" }, target: { node: "END" } },
      { id: "w1", kind: "wiring", source: { node: "r_model" }, target: { node: "n_crew", port: "model" } },
    ],
  };
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: ir.name } })).json();
  const put = await api.put(`/api/v1/workflows/${wf.id}`, { data: { ir: { ...wf.ir, ...ir } }, headers: { "If-Match": String(wf.version) } });
  expect(put.ok(), await put.text()).toBeTruthy();
  await page.goto(`/workflows/${wf.id}`);
  await expect(page.getByTestId("palette-crewai.crew")).toBeVisible();
  await page.getByTestId("node-research").click();
  await expect(page.locator("#cfg_n_crew_agents__title")).toContainText("Agents");
  await expect(page.locator("#cfg_n_crew_tasks_0__title")).toContainText("Task");

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"topic": "bees"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("node-research")).toHaveClass(/status-ok/, { timeout: 30_000 });
  await expect(page.getByTestId("run-output")).toContainText("report");
});
