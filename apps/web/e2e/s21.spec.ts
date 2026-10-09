import { expect, test } from "./fixtures";

// Sprint 21: an Agent block designed as a native agent of another framework, with its code and export.

test("design an agent for another framework, read its code, see where it can run", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Designs ${Date.now()}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await expect(page.locator(".save-state")).toHaveText("Saved");

  await page.getByTestId("palette-core.agent").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  await page.getByLabel("block framework").selectOption("openai_agents");
  await expect(page.locator(".save-state")).toHaveText("Saved");

  const panel = page.getByRole("region", { name: "designed agent" });
  await expect(panel).toContainText("OpenAI Agents SDK");
  await panel.getByRole("button", { name: "View OpenAI Agents SDK code" }).click();
  await expect(panel.getByLabel("OpenAI Agents SDK code")).toContainText("from agents import");
  await expect(panel.getByRole("button", { name: "Export as OpenAI Agents SDK project" })).toBeVisible();

  // only frameworks that can host designed agents run it
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^LangGraph: ready/ })).toBeVisible();
  await expect(badges.getByRole("button", { name: /^Echo \(demo engine\): 1 block it cannot run/ })).toBeVisible();

  // back to the workflow's own framework: the panel goes away
  await page.getByLabel("block framework").selectOption("");
  await expect(panel).toHaveCount(0);
});
