import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// Sprint 4 acceptance: debug & run UX on the support-triage template (the demo model answers "low").

async function openTriage(page: Page) {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
}

async function run(page: Page, ticket: string) {
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill(JSON.stringify({ ticket }));
  await page.getByRole("button", { name: "▶ Run" }).click();
}

test("breakpoint pauses before the router; continue proceeds to the tool approval", async ({ page }) => {
  await openTriage(page);
  await page.getByTestId("node-route_urgency").click();
  await page.getByRole("button", { name: "○ Add breakpoint" }).click();
  await expect(page.getByTestId("node-route_urgency").locator(".bp-dot")).toBeVisible();
  await run(page, "Where is my invoice?");
  await expect(page.getByText("Paused at a breakpoint before route_urgency")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "⏵ Continue" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
});

test("timeline: edit a past checkpoint and fork into the other branch; explain narrates it", async ({ page }) => {
  await openTriage(page);
  await run(page, "Where is my invoice?");
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });

  await page.getByRole("tab", { name: "Explain" }).click();
  await expect(page.getByTestId("explain")).toContainText("chose route 'normal' → reply_drafter");

  await page.getByRole("tab", { name: "Timeline" }).click();
  await page.getByRole("option", { name: /→ route_urgency/ }).click();
  const state = page.getByLabel("checkpoint state");
  await expect(state).toHaveValue(/"urgency": "low"/);
  await expect(page.getByTestId("lineage-urgency")).toContainText("classify"); // FR-X-18
  await expect(page.getByTestId("lineage-ticket")).toContainText("run input");
  await state.fill((await state.inputValue()).replace('"urgency": "low"', '"urgency": "high"'));
  await page.getByRole("button", { name: "⑂ Fork from here" }).click();
  await expect(page.getByTestId("node-escalate")).toHaveClass(/status-ok/, { timeout: 10_000 });
  await expect(page.getByTestId("node-manager_approval")).toHaveClass(/status-interrupted/);
  await page.getByRole("tab", { name: "Explain" }).click();
  await expect(page.getByTestId("explain")).toContainText("chose route 'high' → escalate");
});

test("chat mode keeps a multi-turn conversation on one thread", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-chat_assistant").click();
  await expect(page.getByTestId("node-assistant")).toBeVisible();
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("chat message").fill("Hello there");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".bubble.ai")).toHaveCount(1, { timeout: 10_000 });
  await page.getByLabel("chat message").fill("And again");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".bubble.human")).toHaveCount(2, { timeout: 10_000 });
  await expect(page.locator(".bubble.ai")).toHaveCount(2);
});

test("inbox shows pending approvals across workflows and resumes the run", async ({ page }) => {
  await openTriage(page);
  await run(page, "Inbox please");
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
  // the inbox is shared with tests running in parallel: follow this run's thread, never a count
  const thread = (await page.locator("label.row code").first().textContent())!.trim();
  expect(thread).toMatch(/^th_/);
  await page.getByTestId("inbox-link").click();
  const mine = page.getByTestId("inbox-item").filter({ has: page.locator(`a[href$="thread=${thread}"]`) });
  await expect(mine).toHaveCount(1);
  await mine.getByRole("button", { name: "Approve" }).click();
  await expect(mine).toHaveCount(0, { timeout: 10_000 });
});

test("tidy, live code diff, copy/paste and command palette", async ({ page }) => {
  await openTriage(page);
  await page.getByRole("button", { name: "Tidy" }).click();
  await page.waitForTimeout(500);
  const start = await page.getByTestId("node-START").boundingBox();
  const cls = await page.getByTestId("node-classify").boundingBox();
  expect(start!.x).toBeLessThan(cls!.x);

  // FR-X-01: an edit highlights the generated lines it changed
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByTestId("code-view")).toContainText("escalate");
  await page.getByTestId("node-escalate").click();
  await page.getByRole("tab", { name: "Inspector" }).click();
  await page.getByLabel("node label").fill("Escalate now");
  await page.getByLabel("node label").blur();
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.getByTestId("code-changed")).toContainText("Last edit changed", { timeout: 10_000 });
  await expect(page.locator(".code-view .changed")).toContainText("Escalate now");

  await page.getByTestId("node-classify").click();
  await page.keyboard.press("Control+c");
  await page.keyboard.press("Control+v");
  await expect(page.getByTestId("node-classify_2")).toBeVisible();

  await page.keyboard.press("Control+k");
  await page.getByLabel("Search commands").fill("add set values");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("node-set_values")).toBeVisible();
});

test("pinned output skips the model; mock replies steer a debug run", async ({ page }) => {
  await openTriage(page);
  await run(page, "Where is my invoice?");
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("metrics-classify")).toContainText("tok");

  // pin the classifier's last output: debug runs replay it and never call its model
  await page.getByTestId("node-classify").click();
  await page.getByRole("tab", { name: "Inspector" }).click();
  await page.getByRole("button", { name: "📌 Pin last output" }).click();
  await expect(page.getByTestId("pinned-classify")).toBeVisible();
  await run(page, "Where is my invoice?");
  await expect(page.getByTestId("debug-badge")).toContainText("1 pinned");
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 10_000 });
  // a pinned step has no tokens; its metrics badge may be absent altogether when it took under 1 ms
  await expect(page.getByTestId("node-classify")).not.toContainText("tok");

  // unpin, then mock the fast model with "high": the run takes the escalation branch
  await page.getByTestId("node-classify").click();
  await page.getByRole("tab", { name: "Inspector" }).click();
  await page.getByRole("button", { name: "Unpin" }).click();
  await page.getByTestId("node-fast_model").click();
  await page.getByLabel("mock replies", { exact: true }).fill("high");
  await page.getByLabel("mock replies", { exact: true }).blur();
  await run(page, "Where is my invoice?");
  await expect(page.getByTestId("debug-badge")).toContainText("1 mocked");
  await expect(page.getByTestId("node-escalate")).toHaveClass(/status-ok/, { timeout: 10_000 });
});

test("convert steps to a subgraph, open it, and inline it back", async ({ page }) => {
  await openTriage(page);
  await page.getByTestId("node-escalate").click();
  await page.getByTestId("node-manager_approval").click({ modifiers: ["ControlOrMeta"] });
  await page.getByTestId("more-menu").click();
  await page.getByRole("menuitem", { name: "⧉ Subgraph" }).click();
  await expect(page.getByTestId("node-escalate_group")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("node-escalate")).toHaveCount(0);
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await expect(page.locator(".problems")).not.toContainText("E0");

  // drill in (read-only) and back out through the breadcrumb
  await page.getByTestId("node-escalate_group").dblclick();
  await expect(page.getByRole("navigation", { name: "subgraph path" })).toContainText("Escalate group");
  await expect(page.getByTestId("node-escalate")).toBeVisible();
  await expect(page.getByTestId("node-manager_approval")).toBeVisible();
  await page.getByRole("navigation", { name: "subgraph path" }).getByRole("button", { name: "Workflow" }).click();
  await expect(page.getByTestId("node-escalate_group")).toBeVisible();

  // inline the steps again
  await page.getByTestId("node-escalate_group").click();
  await page.getByRole("tab", { name: "Inspector" }).click();
  await page.getByRole("button", { name: "Inline steps" }).click();
  await expect(page.getByTestId("node-escalate")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("node-escalate_group")).toHaveCount(0);
  await expect(page.locator(".save-state")).toHaveText("Saved");
  await expect(page.locator(".problems")).not.toContainText("E0");
});
