import { expect, type Page, test } from "@playwright/test";

// Sprint 5 acceptance: map-reduce research with a Deep Agent, and the structured-output designer.

const handle = (page: Page, node: string, id: string) =>
  page.locator(`[data-testid="node-${node}"] .react-flow__handle[data-handleid="${id}"]`);

test("research template: map fans out per topic and the Deep Agent saves findings to /memories/", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-research_brief").click();
  await expect(page.getByTestId("node-writer")).toBeVisible();
  await expect(handle(page, "each_topic", "each")).toBeAttached();
  await expect(handle(page, "each_topic", "empty")).toBeAttached();
  await expect(handle(page, "fact_checker", "model")).toBeAttached(); // resources can take inputs (subagent model)
  await expect(page.locator(".problems")).not.toContainText("E0");

  await page.getByRole("tab", { name: "Run" }).click();
  await expect(page.getByLabel("run input")).toHaveValue(/"topics": \[\]/);
  await page.getByLabel("run input").fill(JSON.stringify({ question: "Hot drinks", topics: ["tea", "coffee", "cocoa"] }));
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("node-writer")).toHaveClass(/status-ok/, { timeout: 15_000 });
  await expect(page.getByTestId("node-research")).toHaveClass(/status-ok/);
  await expect(page.getByTestId("run-output")).toContainText("/memories/findings.md");

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByTestId("code-view")).toContainText("ac.map_fn(");
  await expect(page.getByTestId("code-view")).toContainText("ac.deep_agent_node(");

  // memory inspector (M4-T12): the Deep Agent's /memories/ file is a memory of the signed-in user
  await page.getByTestId("memory-link").click();
  await page.getByRole("button", { name: "memories/user/usr_dev" }).click();
  await page.getByLabel("search memories").fill("cocoa");
  const item = page.getByTestId("memory-item").filter({ hasText: "/findings.md" });
  await expect(item).toContainText("Hot drinks");
  await item.getByRole("button", { name: "delete /findings.md" }).click();
  await expect(page.getByTestId("memory-item").filter({ hasText: "/findings.md" })).toHaveCount(0);
});

test("structured output designer adds a schema; the missing channel is reported until declared", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-chat_assistant").click();
  await expect(page.getByTestId("node-assistant")).toBeVisible();
  await page.getByTestId("node-assistant").click();
  await page.getByRole("button", { name: "+ Field" }).click();
  await page.getByLabel("field name", { exact: true }).fill("urgency");
  await page.getByLabel("field type", { exact: true }).selectOption("enum");
  await page.getByLabel("enum options", { exact: true }).fill("low, high");
  await expect(page.locator(".problems")).toContainText("structured_response", { timeout: 5_000 });

  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByRole("button", { name: "+ Channel" }).click();
  const names = page.getByLabel("channel name");
  await names.last().fill("structured_response");
  await names.last().blur();
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible({ timeout: 5_000 });

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByTestId("code-view")).toContainText('"enum": ["low", "high"]');
});
