import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";

// Sprint 31: onboarding and switching. A Langflow export opens and runs on the canvas; n8n imports list what did not
// carry over; the playground turns a chat into a workflow; Simple mode hides advanced blocks; Push to GitHub explains
// the token it needs.

const fixture = (name: string) => readFileSync(new URL(`../../api/tests/importers/${name}.json`, import.meta.url));

test("a Langflow export opens and runs on the canvas", async ({ page }) => {
  await page.goto("/");
  await page.locator("#import-file").setInputFiles({ name: "basic.json", mimeType: "application/json", buffer: fixture("langflow_basic_prompting") });
  await expect(page).toHaveURL(/\/workflows\/wf_/); // nothing to check first: it opens straight away
  await expect(page.getByTestId("node-open_ai")).toBeVisible();
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("chat message").fill("What can I build?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("node-open_ai")).toHaveClass(/status-ok/, { timeout: 30_000 });
});

test("an n8n import lists what did not carry over", async ({ page }) => {
  await page.goto("/");
  await page.locator("#import-file").setInputFiles({ name: "agent.json", mimeType: "application/json", buffer: fixture("n8n_ai_agent") });
  const notes = page.getByTestId("import-notes");
  await expect(notes).toContainText("from n8n");
  await expect(notes).toContainText("Wikipedia");
  await notes.getByRole("link", { name: /Open AI Agent workflow/ }).click();
  await expect(page.getByTestId("node-ai_agent")).toBeVisible();
});

test("the playground chats with a model and becomes a workflow", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("playground-link").click();
  await expect(page.getByTestId("playground")).toBeVisible();
  await page.locator("#pg-model").selectOption({ index: 1 });
  await page.getByLabel("Instructions").fill("Answer in one sentence.");
  await page.getByLabel("Your message").fill("Hello there");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByTestId("pg-log").locator(".bubble.ai")).toHaveCount(1);
  await page.getByLabel("Workflow name").fill(`Playground ${Date.now()}`);
  await page.getByTestId("pg-make").click();
  await expect(page).toHaveURL(/\/workflows\/wf_/);
  await expect(page.getByTestId("node-assistant")).toBeVisible();
});

test("Simple mode shows the beginner blocks and panels; Push to GitHub needs a token", async ({ page }) => {
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", {
    data: { name: `Simple ${Date.now()}`, template: "ticket_triage" },
  })).json();
  await page.goto(`/workflows/${wf.id}`);
  await expect(page.getByTestId("palette-core.subgraph")).toBeVisible();
  await page.getByTestId("simple-toggle").click();
  await expect(page.getByTestId("palette-core.subgraph")).toHaveCount(0);
  await expect(page.getByTestId("palette-core.llm")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Versions" })).toHaveCount(0);
  await expect(page.getByTestId("simple-note")).toBeVisible();
  await page.reload(); // remembered in this browser
  await expect(page.getByTestId("simple-toggle")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("full-toggle").click();
  await expect(page.getByTestId("palette-core.subgraph")).toBeVisible();

  await page.getByRole("tab", { name: "Versions" }).click();
  await page.getByTestId("export-open").click();
  await page.getByTestId("github-push").locator("summary").click();
  await page.locator("#gh-repo").fill("acme/agents");
  await page.getByTestId("github-push-button").click();
  await expect(page.getByTestId("github-push").getByRole("alert")).toContainText("GITHUB_TOKEN");
});
