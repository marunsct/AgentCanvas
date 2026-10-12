import { expect, test } from "./fixtures";

// Sprint 36: a workflow designed for Mastra becomes a TypeScript flow with Mastra's own agents (read in the Code tab)
// and runs on Node.js, a tool call waiting for a person's approval (Mastra's requireApproval) included.

test("switch a template to Mastra, read its TypeScript flow, run it through a tool approval", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByLabel("framework", { exact: true }).selectOption("mastra");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^Mastra: ready/ })).toBeVisible();

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".code-view")).toContainText('import { Agent } from "@mastra/core/agent";');
  await expect(page.locator(".code-view")).toContainText("export const FLOW: rt.Flow = {");
  await expect(page.locator(".code-view")).toContainText('approve: ["send_reply"]');

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("group", { name: "Approval needed" })).toContainText("send_reply");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("the tool call finished (demo model)", { timeout: 30_000 });
  await expect(page.getByTestId("node-reply_drafter")).toHaveClass(/status-ok/);
});
