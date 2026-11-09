import { expect, runOutput, test } from "./fixtures";

// Sprints 29-30: a workflow designed for Google ADK becomes a native ADK graph workflow (read in the Code tab) and
// runs, a tool call waiting for a person's approval included.

test("switch a template to Google ADK, read its workflow, run it through a tool approval", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByLabel("framework", { exact: true }).selectOption("google_adk");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^Google ADK: ready/ })).toBeVisible();

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".code-view")).toContainText("from google.adk import Agent, Workflow");
  await expect(page.locator(".code-view")).toContainText("def build() -> Workflow:");
  await expect(page.locator(".code-view")).toContainText('approve=["send_reply"]');

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("group", { name: "Approval needed" })).toContainText("send_reply");
  await page.getByRole("button", { name: "Approve" }).click();
  const out = await runOutput(page, 30_000); // the approved tool call ran and the run finished with a state, whatever the model said
  expect(Object.keys(out).length, `the finished run has no output: ${JSON.stringify(out)}`).toBeGreaterThan(0);
  await expect(page.getByTestId("node-reply_drafter")).toHaveClass(/status-ok/);
});
