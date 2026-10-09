import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// Sprint 20: a workflow picks its framework, each AI block may name one, and the badges say where it can run.

async function newWorkflow(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("workflow name").fill(name);
  await page.getByRole("button", { name: "Create empty" }).click();
  await expect(page.locator(".save-state")).toHaveText("Saved");
}

test("the badges, the framework picker and a block's own framework", async ({ page }) => {
  await newWorkflow(page, `Frameworks ${Date.now()}`);
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^LangGraph: ready/ })).toBeVisible();
  await expect(badges.getByRole("button", { name: /^Echo \(demo engine\): ready/ })).toBeVisible();

  // an AI agent is a LangGraph block: Echo cannot run it, and its badge leads to it
  await page.getByTestId("palette-core.agent").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  const echo = badges.getByRole("button", { name: /^Echo \(demo engine\): 1 block it cannot run/ });
  await expect(echo).toBeVisible();
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await echo.click();
  await expect(page.getByLabel("node name")).toHaveValue(/agent/);

  // switching the workflow to Echo flags the block (P001); undo switches back
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByLabel("framework", { exact: true }).selectOption("echo");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await page.getByRole("tab", { name: "Problems", exact: true }).click();
  await expect(page.locator(".problems")).toContainText("P001");
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByLabel("framework", { exact: true })).toHaveValue("langchain");

  // an AI block can be designed for another framework: it still runs on the workflow's (P004)
  await page.getByTestId("palette-core.llm").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 330 } });
  await page.getByLabel("block framework").selectOption("echo");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await expect(page.locator(".problems")).toContainText("P004");
  await page.getByLabel("block framework").selectOption("");
  await expect(page.locator(".problems")).not.toContainText("P004");
});
