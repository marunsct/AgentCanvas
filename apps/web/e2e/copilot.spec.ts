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
