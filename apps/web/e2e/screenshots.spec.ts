import { resolve } from "node:path";
import { expect, test } from "./fixtures";

// Retakes the pictures in docs/user-guide/img. Not part of the normal run: `make screenshots` (SCREENSHOTS=1) runs
// it against the fake-model server, so the pictures show made-up data and no real keys or people.
test.beforeEach(() => {
  test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 (make screenshots) to retake the user guide pictures");
});

const IMG = resolve(process.cwd(), "../../docs/user-guide/img");
const shot = (page: import("@playwright/test").Page, name: string) =>
  page.screenshot({ path: `${IMG}/${name}.png`, animations: "disabled" });

test.use({ viewport: { width: 1300, height: 800 }, colorScheme: "light" });

test("home", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("template-classify_and_route")).toBeVisible();
  await shot(page, "home");
});

test("editor after a run", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-classify_and_route").click();
  await expect(page.getByTestId("node-fast_model")).toBeVisible();
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"request": "Where is my order?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("run-usage")).toBeVisible();
  await page.mouse.move(5, 5);
  await shot(page, "editor-run");
});

test("a run waiting for approval", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-support_triage").click();
  await expect(page.getByTestId("node-reply_drafter")).toBeVisible();
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"ticket": "Where is my invoice?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByRole("group", { name: "Approval needed" })).toBeVisible({ timeout: 15_000 });
  await shot(page, "approval");
});

test("evaluation", async ({ page }) => {
  const api = page.request;
  const wf = await (await api.post("/api/v1/projects/prj_default/workflows", { data: { name: "Order questions", template: "classify_and_route" } })).json();
  const ds = await (await api.post(`/api/v1/workflows/${wf.id}/datasets`, { data: { name: "Customer requests" } })).json();
  const requests = ["Where is my order?", "My parcel never came", "I want a refund", "Change my delivery address"];
  await api.post(`/api/v1/datasets/${ds.id}/examples`, { data: { examples: requests.map((request) => ({ input: { request } })) } });
  await api.post(`/api/v1/workflows/${wf.id}/evaluators`, { data: { name: "answered", kind: "regex", config: { channel: "reply", pattern: "." } } });
  await page.goto(`/workflows/${wf.id}/evaluation`);
  await page.getByRole("tab", { name: "Experiments" }).click();
  await page.getByRole("button", { name: "▶ Run experiment" }).click();
  await expect(page.getByTestId("results").locator("thead")).toContainText("100%", { timeout: 20_000 });
  await expect(page.getByTestId("experiments").locator("tr", { hasText: "running" })).toHaveCount(0, { timeout: 20_000 });
  await shot(page, "evaluation");
});

test("workspace", async ({ page }) => {
  const put = (path: string, content: string) => page.request.put(`/api/v1/projects/prj_default/files/${path}`, { data: { content } });
  await put("nodes/shout.py", 'def run(inputs, config=None):\n    """Upper-case the text it is given."""\n    return {"text": str(inputs.get("text", "")).upper() + "!"}\n');
  await put("scripts/textutil.py", 'def shout(text: str) -> str:\n    return text.upper() + "!"\n');
  await page.goto("/workspace");
  await page.getByText("shout.py").first().click();
  await expect(page.locator(".monaco-editor, .cm-editor, textarea").first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(800);
  await shot(page, "workspace");
});

for (const [page_, name] of [["models", "setup"], ["members", "members"], ["policies", "policies"]] as const) {
  test(`settings ${page_}`, async ({ page }) => {
    await page.goto(`/settings/${page_}`);
    await page.waitForLoadState("load");
    await page.waitForTimeout(800);
    await shot(page, name);
  });
}
