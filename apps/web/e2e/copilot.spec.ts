import { expect, test } from "./fixtures";

// The Copilot (S14): ask it to build, see the proposal on the canvas, accept it, and undo it in one step.

test("the Copilot builds a workflow the person accepts, and undo takes it back", async ({ page }) => {
  const settings = await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  expect(settings.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();

  await page.getByTestId("copilot-button").click();
  await expect(page.getByTestId("copilot-panel")).toContainText("claude-haiku-4-5");
  await page.getByLabel("Message the Copilot").fill("Build a workflow that greets people");
  await page.getByRole("button", { name: "Send" }).click();

  // the proposal is previewed on the canvas, nothing saved yet
  await expect(page.getByTestId("copilot-proposal")).toContainText("Add a Greet step");
  await expect(page.getByTestId("ghost-banner")).toBeVisible();
  await expect(page.getByTestId("node-greet")).toBeVisible();
  await expect(page.getByTestId("copilot-panel")).toContainText("I proposed a Greet step");

  await page.getByTestId("copilot-proposal").getByRole("button", { name: "Accept" }).click();
  await expect(page.getByTestId("ghost-banner")).toHaveCount(0);
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await expect(page.getByTestId("node-greet")).toBeVisible();
  await expect(page.getByTestId("copilot-proposal")).toHaveCount(0);

  await page.locator(".react-flow__pane").click();
  await page.keyboard.press("Control+z");
  await expect(page.getByTestId("node-greet")).toHaveCount(0);
});

test("Ask mode answers without changing anything", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot ask ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.getByTestId("copilot-button").click();
  await page.getByRole("radio", { name: "Ask" }).click();
  await page.getByLabel("Message the Copilot").fill("What can you do?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("copilot-panel")).toContainText("demo Copilot");
  await expect(page.getByTestId("copilot-proposal")).toHaveCount(0);
});

test("the Problems list hands its errors to the Copilot", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", {
    data: { source: "separate", model: "anthropic:claude-haiku-4-5" },
  });
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Copilot fix ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.locator('[data-testid="node-START"] .react-flow__handle').click();
  await page.getByTestId("port-add-core.llm").click(); // an Ask AI step without a model: a problem
  await page.getByTestId("ask-copilot-fix").click();
  await expect(page.getByTestId("copilot-panel")).toContainText("Find and fix the problems");
  await expect(page.getByTestId("copilot-panel")).toContainText("demo Copilot");
});

test("the person keeps part of a proposal: unticking a step drops its wires, and the canvas shows the rest", async ({ page }) => {
  await page.request.put("/api/v1/projects/prj_default/copilot-settings", { data: { source: "separate", model: "anthropic:claude-haiku-4-5" } });
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name: `Partial ${Date.now()}` } })).json();
  await page.goto(`/workflows/${wf.id}`);
  await page.getByTestId("copilot-button").click();
  await page.getByLabel("Message the Copilot").fill("Build a workflow that greets people");
  await page.getByRole("button", { name: "Send" }).click();
  const card = page.getByTestId("copilot-proposal");
  await expect(card).toContainText("Add a Greet step");
  await expect(page.getByTestId("node-greet")).toBeVisible();

  await card.getByLabel("Add the step “Greet”").uncheck();
  await expect(card.getByLabel("Connect Start to “Greet”")).not.toBeChecked(); // its wires go with it
  await expect(page.getByTestId("node-greet")).toHaveCount(0); // and the preview no longer has it
  await card.getByRole("button", { name: "Accept the ticked changes" }).click();
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const saved = await (await page.request.get(`/api/v1/workflows/${wf.id}`)).json();
  expect(saved.ir.nodes).toEqual([]);
  expect(saved.ir.resources.map((r: { name: string }) => r.name)).toEqual(["demo_model"]);
});
