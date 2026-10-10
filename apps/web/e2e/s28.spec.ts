import { expect, test } from "./fixtures";

// Sprint 28: a mixed flow (an OpenAI Agents SDK researcher, a CrewAI writer) runs; the CrewAI agent is a part with an
// environment of its own, and its typed answer crosses back checked.

test("a mixed-framework template runs, with its CrewAI writer as a part", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-mixed_team").click();
  await expect(page.getByTestId("node-writer")).toBeVisible();
  await page.getByTestId("node-writer").click();
  await expect(page.getByTestId("part-note")).toContainText("runs as a part");
  await expect(page.getByTestId("part-note")).toContainText("its structured output, checked");
  await page.getByTestId("node-researcher").click();
  await expect(page.getByTestId("part-note")).toHaveCount(0); // the OpenAI Agents SDK fits next to LangGraph

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("chat message").fill("Honey bees in cities");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("node-writer")).toHaveClass(/status-ok/, { timeout: 60_000 });
  await expect(page.locator(".bubble.ai").last()).toContainText("summary"); // the writer's checked, typed brief
});
