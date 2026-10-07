import type { Locator, Page } from "@playwright/test";
import { expect, test } from "./fixtures";

// Sprint 3 acceptance: build a workflow from an empty canvas with the mouse, see validation, run it, reload it.

const handle = (page: Page, node: string, id: string) =>
  page.locator(`[data-testid="node-${node}"] .react-flow__handle[data-handleid="${id}"]`);

async function connect(page: Page, from: Locator, to: Locator) {
  // measure handles only once edits have landed: server diagnostics add/remove badges and can move handles
  await expect(page.locator(".save-state")).toHaveText("Saved");
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 });
  await page.mouse.up();
}

async function dropWidget(page: Page, type: string, x: number, y: number) {
  await page.getByTestId(`palette-${type}`).dragTo(page.locator(".react-flow__pane"), { targetPosition: { x, y } });
}

async function createEmpty(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("workflow name").fill(name);
  await page.getByRole("button", { name: "Create empty" }).click();
  await expect(page.locator(".crumb")).toHaveText(`/ ${name}`);
}

test("build, validate, run and reload a workflow from an empty canvas", async ({ page }) => {
  const warnings: string[] = [];
  page.on("console", (m) => m.type() === "warning" && warnings.push(m.text().slice(0, 300)));
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await createEmpty(page, `E2E build ${Date.now()}`);

  // state channels: question -> answer
  await page.getByRole("button", { name: "+ Channel" }).click();
  await page.getByRole("button", { name: "+ Channel" }).click();
  const names = page.getByLabel("channel name");
  await names.nth(0).fill("question");
  await names.nth(0).blur();
  await expect(names.nth(0)).toHaveValue("question");
  await names.nth(1).fill("answer");
  await names.nth(1).blur();
  await expect(page.locator(".save-state")).toHaveText("Saved");

  // widgets
  await dropWidget(page, "core.llm", 330, 180);
  await expect(page.getByTestId("node-ask_ai")).toBeVisible();
  await page.locator("textarea[id^='cfg_']").fill("Answer briefly: {{ state.question }}");
  await page.locator("select[id$='_output']").selectOption("answer");

  await dropWidget(page, "model.chat", 150, 380);
  await expect(page.getByTestId("node-ai_model")).toBeVisible();
  await page.locator("input[id$='_model']").fill("anthropic:claude-haiku-4-5");

  // a wrong wire is refused with an explanation: model -> nothing typed "Tool" here, so try START -> model port
  await connect(page, handle(page, "ai_model", "model"), page.getByTestId("node-END"));
  await expect(page.locator(".react-flow__edge")).toHaveCount(0);

  // wiring and flow
  await connect(page, handle(page, "ai_model", "model"), handle(page, "ask_ai", "model"));
  await connect(page, handle(page, "START", "next"), handle(page, "ask_ai", "in"));
  await connect(page, handle(page, "ask_ai", "next"), handle(page, "END", "in"));
  await expect(page.locator(".react-flow__edge")).toHaveCount(3);
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible({ timeout: 10_000 });

  // run with the demo model
  await page.getByRole("tab", { name: "Run" }).click();
  await page.getByLabel("run input").fill('{"question": "What is 2+2?"}');
  await page.getByRole("button", { name: "▶ Run" }).click();
  await expect(page.getByTestId("run-output")).toContainText("Demo reply", { timeout: 15_000 });
  await expect(page.getByTestId("node-ask_ai")).toHaveClass(/status-ok/);

  // generated code shows the node
  await page.getByRole("tab", { name: "Code" }).click();
  await expect(page.locator(".code-view")).toContainText('"ask_ai"');

  // everything was autosaved
  await page.reload();
  await expect(page.getByTestId("node-ask_ai")).toBeVisible();
  try {
    await expect(page.locator(".react-flow__edge")).toHaveCount(3);
  } catch (e) {
    // diagnostics for CI logs (traces are not always reachable): saved edges vs rendered handles
    const id = page.url().split("/workflows/")[1];
    const saved = await (await page.request.get(`/api/v1/workflows/${id}`)).json();
    console.log("saved edges:", JSON.stringify(saved.ir.edges));
    console.log("handles:", await page.locator(".react-flow__handle").evaluateAll((hs) =>
      hs.map((h) => `${h.closest(".react-flow__node")?.getAttribute("data-id")}:${h.getAttribute("data-handleid")}`)));
    console.log("warnings:", JSON.stringify(warnings));
    throw e;
  }
  expect(errors).toEqual([]);
});

test("missing wiring shows E001 and deleting a wire brings it back", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("template-classify_and_route").click();
  await expect(page.getByTestId("node-classify")).toBeVisible();
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible();
  await page.getByTestId("node-fast_model").click();
  await page.keyboard.press("Delete");
  await expect(page.locator(".problems")).toContainText("E001", { timeout: 5_000 });
  await expect(page.getByTestId("node-classify")).toContainText("⛔");
  // undo restores the resource and its wires
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.keyboard.press("Control+z");
  await expect(page.getByText("No problems — the workflow can run.")).toBeVisible({ timeout: 5_000 });
});

test("concurrent edits in two tabs produce a conflict notice instead of a silent overwrite", async ({ browser }) => {
  const ctx = await browser.newContext();
  const a = await ctx.newPage();
  await createEmpty(a, `Conflict ${Date.now()}`);
  const url = a.url();
  const b = await ctx.newPage();
  await b.goto(url);
  await expect(b.getByRole("button", { name: "+ Channel" })).toBeVisible();
  await a.getByRole("button", { name: "+ Channel" }).click();
  await expect(a.locator(".save-state")).toHaveText("Saved");
  await b.getByRole("button", { name: "+ Chat preset" }).click();
  await expect(b.getByRole("status")).toContainText("Updated elsewhere");
  await ctx.close();
});
