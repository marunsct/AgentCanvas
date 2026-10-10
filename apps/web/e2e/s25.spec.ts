import { expect, test } from "./fixtures";

// Sprint 25: a workflow designed for the OpenAI Agents SDK runs as SDK code (tool approvals included), and an
// experiment runs the same workflow on LangGraph and on the SDK side by side.

test("switch a template to the OpenAI Agents SDK, read its code, run it through a tool approval", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByLabel("framework", { exact: true }).selectOption("openai_agents");
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const badges = page.getByRole("list", { name: "frameworks this workflow can run on" });
  await expect(badges.getByRole("button", { name: /^OpenAI Agents SDK: ready/ })).toBeVisible();

  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".code-view")).toContainText("from agents import Agent");
  await expect(page.locator(".code-view")).toContainText("def reply_drafter_agent(run: rt.Run) -> Agent:");

  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("group", { name: "Approval needed" })).toContainText("send_reply");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("run-output")).toContainText("the tool call finished (demo model)", { timeout: 15_000 });
  await expect(page.getByTestId("node-reply_drafter")).toHaveClass(/status-ok/);
});

test("convert and compare: one experiment runs the workflow on LangGraph and on the OpenAI Agents SDK", async ({ page }) => {
  const api = page.request;
  const name = `Compare ${Date.now()}`;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name, template: "classify_and_route" } })).json();
  const ds = await (await api.post(`/api/v1/workflows/${wf.id}/datasets`, { data: { name: "Requests" } })).json();
  const examples = ["Where is my order?", "My parcel never came"].map((request) => ({ input: { request } }));
  expect((await api.post(`/api/v1/datasets/${ds.id}/examples`, { data: { examples } })).ok()).toBeTruthy();
  const evaluator = { name: "answered", kind: "regex", config: { channel: "reply", pattern: "." } };
  expect((await api.post(`/api/v1/workflows/${wf.id}/evaluators`, { data: evaluator })).ok()).toBeTruthy();

  await page.goto(`/workflows/${wf.id}/evaluation`);
  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByRole("button", { name: "+ Add a version or variant" }).click();
  await page.getByLabel("target 2 framework").selectOption("openai_agents");
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  const results = page.getByTestId("results");
  await expect(results.locator("thead")).toContainText("draft on OpenAI Agents SDK", { timeout: 20_000 });
  await expect(page.getByTestId("experiments").locator("tr", { hasText: "running" })).toHaveCount(0, { timeout: 20_000 });
  await expect(results.locator("thead")).not.toContainText("error");
  await expect(results.locator("thead")).toContainText("100%");
});
