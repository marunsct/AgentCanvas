import { expect, test } from "./fixtures";

// Beginner help: the guide opens on a first visit, ports explain themselves on hover, and clicking a port adds
// the next block already connected, until the workflow can run — without dragging a single wire.

test("a beginner builds a runnable workflow by clicking ports", async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.removeItem("agentcanvas.helpSeen"); } catch { /* ignore */ } });
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Beginner ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();

  // first visit to an empty workflow: the guide opens by itself, and stays closed once dismissed
  await expect(page.getByTestId("help-drawer")).toContainText("What you are building");
  await page.getByRole("button", { name: "Got it" }).click();
  await expect(page.getByTestId("help-drawer")).toHaveCount(0);

  await page.getByRole("button", { name: "+ Channel" }).click();
  await page.getByRole("button", { name: "+ Channel" }).click();
  await page.getByLabel("channel name").nth(0).fill("question");
  await page.getByLabel("channel name").nth(0).blur();
  await page.getByLabel("channel name").nth(1).fill("answer");
  await page.getByLabel("channel name").nth(1).blur();
  await expect(page.locator(".save-state")).toHaveText("Saved");

  // Start → "what can come next?" → Ask AI, added and connected
  await page.locator('[data-testid="node-START"] .react-flow__handle').click();
  await expect(page.getByTestId("port-menu")).toContainText("What can come next?");
  await page.getByTestId("port-add-core.llm").click();
  await expect(page.getByTestId("node-ask_ai")).toBeVisible();
  await expect(page.locator(".react-flow__edge")).toHaveCount(1);
  await page.locator("textarea[id^='cfg_']").fill("Answer simply: {{ state.question }}");
  await page.locator("select[id$='_output']").selectOption("answer");

  // the missing model is explained in plain words
  await expect(page.locator(".problems")).toContainText("How to fix:", { timeout: 10_000 });

  // hovering a port says what it is for
  const modelPort = page.locator('[data-testid="node-ask_ai"] .port-row.in').first();
  await modelPort.hover();
  await expect(modelPort.locator(".port-tip")).toBeVisible();
  await expect(modelPort.locator(".port-tip")).toContainText("AI model");

  // model input → AI model, wired; next → Finish
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await page.locator('[data-testid="node-ask_ai"] .react-flow__handle[data-handleid="model"]').click();
  await page.getByTestId("port-add-model.chat").click();
  await expect(page.getByTestId("node-ai_model")).toBeVisible();
  await page.locator("input[id$='_model']").fill("anthropic:claude-haiku-4-5");
  await page.locator("input[id$='_model']").blur();
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await page.locator('[data-testid="node-ask_ai"] .react-flow__handle[data-handleid="next"]').click();
  await page.getByTestId("port-existing-END").click();
  await expect(page.locator(".react-flow__edge")).toHaveCount(3);
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible({ timeout: 10_000 });

  // the guide is always one click away
  await page.getByTestId("help-button").click();
  await expect(page.getByTestId("help-drawer")).toBeVisible();
});
