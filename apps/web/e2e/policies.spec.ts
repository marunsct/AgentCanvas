import { answerDialog, expect, test } from "./fixtures";

// Sprint 10 (FR-X-19): an admin adds an organisation policy; the editor flags workflows that break it.

test("add a policy, see it in the editor, switch it off and delete it", async ({ page }) => {
  // the policy only looks at this test's workflow, so parallel journeys keep a clean problems panel
  const name = `Policy demo ${Date.now()}`;
  const wf = await (await page.request.post("/api/v1/projects/prj_default/workflows", { data: { name } })).json();
  await page.goto("/");
  await page.getByTestId("settings-link").click();
  await page.getByTestId("settings-policies").click();
  await page.getByLabel("policy name").fill(`Needs a person ${name}`);
  await page.getByLabel("scope").selectOption("workflow");
  await page.getByLabel("match").fill(`workflow.name == "${name}"`);
  await page.getByLabel("require").fill('workflow.node_types.exists(t, t == "core.approval")');
  await page.getByLabel("description").fill("a person approves before anything happens");
  await page.getByRole("button", { name: "Add policy" }).click();
  const row = page.getByTestId("policies").locator("tr", { hasText: name });
  await expect(row).toContainText("a person approves");

  await page.goto(`/workflows/${wf.id}`);
  await expect(page.locator(".problems")).toContainText("E095");
  await expect(page.locator(".problems")).toContainText("a person approves before anything happens");

  await page.goto("/policies");
  await row.getByRole("button", { name: "Switch off" }).click();
  await expect(row.getByRole("button", { name: "Switch on" })).toBeVisible();
  await page.goto(`/workflows/${wf.id}`);
  await expect(page.locator(".problems")).not.toContainText("E095");

  await page.goto("/policies");
  await row.getByRole("button", { name: "Delete" }).click();
  await answerDialog(page);
  await expect(row).toHaveCount(0);
});

test("a policy that does not compile is refused", async ({ page }) => {
  await page.goto("/policies");
  await page.getByLabel("policy name").fill(`Broken ${Date.now()}`);
  await page.getByLabel("require").fill("node.type ==");
  await page.getByRole("button", { name: "Add policy" }).click();
  await expect(page.getByRole("alert")).toContainText("invalid condition");
});
