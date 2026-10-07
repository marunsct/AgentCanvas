import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// Sprint 7 acceptance (J3 part 1): a Script node backed by a workspace file, edited in the browser, runs.

const handle = (page: Page, node: string, id: string) =>
  page.locator(`[data-testid="node-${node}"] .react-flow__handle[data-handleid="${id}"]`);

async function connect(page: Page, from: Locator, to: Locator) {
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
}

async function typeCode(page: Page, code: string) {
  await page.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.press("Delete");
  await page.keyboard.insertText(code);
}

test("script node: scaffold its file, edit it with ruff feedback, run the new code", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Script ${stamp}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.getByRole("button", { name: "+ Channel" }).click();
  await page.getByLabel("channel name").first().fill("text");
  await page.getByLabel("channel name").first().blur();
  await expect(page.locator(".save-state")).toHaveText("Saved");

  await page.getByTestId("palette-core.script").dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  await expect(page.getByTestId("node-run_python_code")).toBeVisible();
  await page.locator("input[id$='_import_path']").fill("");
  await page.locator("input[id$='_import_path']").blur();
  // a unique node name keeps parallel runs of this journey apart (one shared workspace)
  await page.getByLabel("node name").fill(`shout_${stamp}`);
  await page.getByLabel("node name").blur();
  await page.getByRole("button", { name: `+ Create scripts/shout_${stamp}.py` }).click();
  await expect(page.getByTestId("open-script")).toBeVisible();

  await connect(page, handle(page, "START", "next"), handle(page, `shout_${stamp}`, "in"));
  await connect(page, handle(page, `shout_${stamp}`, "next"), handle(page, "END", "in"));
  await expect(page.locator(".react-flow__edge")).toHaveCount(2);
  const editorUrl = page.url();

  // edit in the workspace: ruff flags the undefined name, the fix clears it
  await page.getByTestId("open-script").click();
  await expect(page.getByTestId("code-editor")).toContainText("def run(state: dict) -> dict:");
  await typeCode(page, "def run(state):\n    return {'text': state['text'].upper() + suffix}\n");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("lint")).toContainText("F821");
  await typeCode(page, "def run(state):\n    return {'text': state['text'].upper() + '!'}\n");
  await page.keyboard.press("ControlOrMeta+s");
  await expect(page.getByTestId("lint")).toContainText("ruff: no problems");

  await page.goto(editorUrl);
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"text": "hello"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("HELLO!", { timeout: 10_000 });
});

test("custom node from the workspace appears in the palette and runs", async ({ page }) => {
  const stamp = Date.now();
  const type = `acme.greet_${stamp}`;
  const src = [
    "from agentcanvas.sdk import canvas_node",
    "",
    "",
    `@canvas_node(name="${type}", label="Greet ${stamp}", category="Acme", writes=["text"],`,
    '             config={"type": "object", "properties": {"greeting": {"type": "string", "default": "Hello"}}})',
    "def greet(state, cfg):",
    "    return {'text': f\"{cfg.get('greeting', 'Hello')}, {state.get('text')}!\"}",
    "",
  ].join("\n");
  const saved = await page.request.put(`/api/v1/projects/prj_default/files/nodes/greet_${stamp}.py`, { data: { content: src } });
  expect(saved.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByLabel("workflow name").fill(`Custom ${stamp}`);
  await page.getByRole("button", { name: "Create empty" }).click();
  await page.getByRole("button", { name: "+ Channel" }).click();
  await page.getByLabel("channel name").first().fill("text");
  await page.getByLabel("channel name").first().blur();
  await expect(page.locator(".save-state")).toHaveText("Saved");

  const item = page.getByTestId(`palette-${type}`);
  await expect(item).toContainText("workspace");
  await item.dragTo(page.locator(".react-flow__pane"), { targetPosition: { x: 330, y: 180 } });
  const nodeName = `greet_${stamp}`;
  await expect(page.getByTestId(`node-${nodeName}`)).toBeVisible();
  await page.locator("input[id$='_greeting']").fill("Hi");
  await page.locator("input[id$='_greeting']").blur();

  await connect(page, handle(page, "START", "next"), handle(page, nodeName, "in"));
  await connect(page, handle(page, nodeName, "next"), handle(page, "END", "in"));
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"text": "Ada"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Hi, Ada!", { timeout: 10_000 });
});

test("secrets are write-only in the workspace", async ({ page }) => {
  const name = `E2E_TOKEN_${Date.now()}`;
  await page.goto("/workspace");
  await page.getByLabel("secret name").fill(name);
  await page.getByLabel("secret value").fill("s3cr3t-value-e2e");
  await page.getByRole("button", { name: "Save secret" }).click();
  await expect(page.getByRole("region", { name: "secrets" })).toContainText(name);
  await expect(page.locator("body")).not.toContainText("s3cr3t-value-e2e");
  await page.getByRole("button", { name: `delete secret ${name}` }).click();
  await expect(page.getByRole("region", { name: "secrets" })).not.toContainText(name);
});

test("the sandbox policy is scaffolded and checked", async ({ page }) => {
  await page.request.delete("/api/v1/projects/prj_default/files/sandbox.json"); // fresh
  await page.goto("/workspace");
  await page.getByRole("button", { name: "Sandbox policy" }).click();
  await expect(page.getByTestId("code-editor")).toContainText('"egress": []');
  await expect(page.getByTestId("lint")).toContainText("no problems");
  const policy = { egress: [], inject: [{ name: "crm", hosts: ["api.crm.example.com"], headers: { Authorization: "Bearer {{ secret.NOPE_E2E }}" } }] };
  await typeCode(page, JSON.stringify(policy));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("lint")).toContainText("E083"); // the project has no such secret
  await expect(page.getByTestId("lint")).toContainText("W082"); // in-process scripts cannot enforce egress
  await page.request.delete("/api/v1/projects/prj_default/files/sandbox.json");
});
